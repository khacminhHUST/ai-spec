"""Assistant graph: prepare → agent → save. Mọi câu hỏi đi vào agent; tra tài liệu dự án là 1 tool của agent
(Query Pipeline N1–N6), nguồn ngoài (MCP) mở theo danh mục.

Checkpointer: thread_id = conversation_id. `messages`, `active_projects`, `open_sources` ở state gốc, giữ giữa các lượt
(`messages` cùng định dạng hội thoại cũ của Query Pipeline). Trạng thái nội bộ agent (tool calls) nằm ở namespace con
của node `agent`. Sự kiện stream (custom): {"event": status|token|token_reset|tool_start|tool_end, "data": ...}.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterator, TypedDict
from urllib.parse import quote

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.services.agent.loop.agent import agent_result, changed_files, output_snapshot, stream_agent
from app.services.agent.loop.confirm import pending_confirms, resume_command
from app.services.agent.runtime import AgentRuntime
from app.services.agent.state import AgentContext, turn_messages
from app.services.query.deps import Deps
from app.services.query.graph import turn_config

log = logging.getLogger(__name__)

WAITING_CONFIRM = "WAITING_CONFIRM"


class AssistantState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    question: str
    attachments: list[str]
    messages: list[dict]
    active_projects: list[str]
    open_sources: list[str]
    output_before: dict
    status: str | None
    answer: str
    citations: list[dict]
    files: list[dict]
    stop_reason: str | None


def file_url(conversation_id: str, user_id: str, name: str) -> str:
    return f"/conversations/{quote(conversation_id)}/files/{quote(name)}?user_id={quote(user_id)}"


def build_assistant_graph(deps: Deps, runtime: AgentRuntime, checkpointer: Any = None):
    manager, cfg = runtime.manager, runtime.cfg

    def emit(event: str, data: Any) -> None:
        get_stream_writer()({"event": event, "data": data})

    def prepare_node(state: AssistantState) -> dict:
        """Tách khỏi node agent: chụp danh sách file đúng 1 lần, không chụp lại khi resume sau xác nhận.
        Ghi hội thoại ngay từ đây để giữ quyền sở hữu trong lúc chờ xác nhận tool write."""
        emit("status", {"step": "agent"})
        msgs = state.get("messages") or []
        try:
            deps.repo.upsert_conversation(state["conversation_id"], state["user_id"],
                                          msgs[0]["content"] if msgs else state["question"])
        except Exception as e:  # noqa: BLE001
            log.error("save conversation lỗi: %s", e)
        reset = {"status": None, "answer": "", "citations": [], "files": [], "stop_reason": None}
        try:
            return {**reset, "output_before": output_snapshot(manager, state["conversation_id"])}
        except Exception as e:  # noqa: BLE001
            log.warning("đọc danh sách file sandbox lỗi: %s", e)
            return {**reset, "output_before": {}}

    def agent_node(state: AssistantState) -> dict:
        ctx = AgentContext(state["user_id"], state["conversation_id"], list(state.get("active_projects") or []),
                           list(state.get("messages") or []), runtime.turn_sources(state["user_id"]),
                           list(state.get("open_sources") or []))
        final, streamed = stream_agent(runtime.agent, ctx, state["question"], state.get("attachments"),
                                       int(cfg["agent"]["max_steps"]), emit)
        res = agent_result(final)
        if streamed.strip() != res["answer"]:        # câu trả lời cuối khác phần đã stream (VD dừng vì giới hạn)
            if streamed:
                emit("token_reset", {})
            emit("token", res["answer"])
        status = "PARTIAL" if res["stop_reason"] else "OK"
        return {"answer": res["answer"], "citations": res["citations"], "status": status,
                "stop_reason": res["stop_reason"],
                "active_projects": res["active_projects"] or list(state.get("active_projects") or []),
                "open_sources": res["open_sources"] or list(state.get("open_sources") or [])}

    def save_node(state: AssistantState) -> dict:
        emit("status", {"step": "save"})
        cid, uid = state["conversation_id"], state["user_id"]
        try:
            files = [{**f, "url": file_url(cid, uid, f["name"])}
                     for f in changed_files(state.get("output_before") or {}, output_snapshot(manager, cid))]
        except Exception as e:  # noqa: BLE001
            log.warning("đọc file kết quả lỗi: %s", e)
            files = []
        msgs = [*(state.get("messages") or []),
                *turn_messages(state["question"], state.get("answer") or "", status=state.get("status"),
                               citations=state.get("citations"), files=files, attachments=state.get("attachments"))]
        try:
            deps.repo.upsert_conversation(cid, uid, msgs[0]["content"] if msgs else None)
        except Exception as e:  # noqa: BLE001
            log.error("save conversation lỗi: %s", e)
        return {"messages": msgs, "files": files}

    g = StateGraph(AssistantState)
    g.add_node("prepare", prepare_node)
    g.add_node("agent", agent_node)
    g.add_node("save", save_node)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "agent")
    g.add_edge("agent", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=checkpointer)


# -------------------------------------------------------------------- chạy

def _stream(graph, inp: Any, conversation_id: str) -> Iterator[dict]:
    """Sự kiện custom + kết thúc lượt: confirm_required (đang chờ) hoặc citations · file; luôn có done."""
    cfg = turn_config(conversation_id)
    yield from graph.stream(inp, cfg, stream_mode="custom")
    snap = graph.get_state(cfg)
    st = snap.values
    done = {"conversation_id": conversation_id, "active_projects": st.get("active_projects") or []}
    pending = pending_confirms(snap.interrupts)
    if pending:
        for p in pending:
            yield {"event": "confirm_required", "data": p}
        yield {"event": "done", "data": {**done, "status": WAITING_CONFIRM}}
        return
    yield {"event": "citations", "data": st.get("citations") or []}
    for f in st.get("files") or []:
        yield {"event": "file", "data": f}
    yield {"event": "done", "data": {**done, "status": st.get("status")}}


def stream_assistant_turn(graph, user_id: str, question: str, conversation_id: str | None = None,
                          attachments: list[str] | None = None) -> Iterator[dict]:
    """Sự kiện: status · token · token_reset · tool_start · tool_end · citations · file · confirm_required · done."""
    cid = conversation_id or uuid.uuid4().hex
    inp = {"conversation_id": cid, "user_id": user_id, "question": question, "attachments": attachments or []}
    yield from _stream(graph, inp, cid)


def stream_confirm(graph, conversation_id: str, confirm_id: str, approve: bool) -> Iterator[dict]:
    """Tiếp tục lượt đang chờ xác nhận tool write."""
    yield from _stream(graph, resume_command(confirm_id, approve), conversation_id)


def pending_confirm_ids(graph, conversation_id: str) -> list[str]:
    return [p["confirm_id"] for p in pending_confirms(graph.get_state(turn_config(conversation_id)).interrupts)]

