"""Assistant graph: gate → lookup (Query Pipeline, giữ nguyên) | agent_prepare → agent → save.

Checkpointer: thread_id = conversation_id. `messages`, `active_projects` ở state gốc, dùng chung hai tuyến (tương thích
hội thoại cũ của Query Pipeline). Trạng thái nội bộ agent (tool calls) nằm ở namespace con của node `agent`.
Sự kiện stream (custom): {"event": status|token|route|tool_start|tool_end, "data": ...}.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterator, TypedDict
from urllib.parse import quote

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.services.agent.gate import run_gate
from app.services.agent.loop.agent import agent_result, changed_files, output_snapshot, stream_agent
from app.services.agent.loop.confirm import pending_confirms, resume_command
from app.services.agent.runtime import AgentRuntime
from app.services.agent.state import AgentContext, turn_messages
from app.services.query.deps import Deps
from app.services.query.graph import build_graph, turn_config

log = logging.getLogger(__name__)

WAITING_CONFIRM = "WAITING_CONFIRM"


class AssistantState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    question: str
    attachments: list[str]
    force_route: str | None
    messages: list[dict]
    active_projects: list[str]
    route: str
    gate_reason: str
    output_before: dict
    status: str | None
    answer: str
    citations: list[dict]
    files: list[dict]
    stop_reason: str | None


def file_url(conversation_id: str, user_id: str, name: str) -> str:
    return f"/conversations/{quote(conversation_id)}/files/{quote(name)}?user_id={quote(user_id)}"


def build_assistant_graph(deps: Deps, runtime: AgentRuntime, checkpointer: Any = None):
    # checkpointer=False: tuyến lookup không có interrupt, không lưu trạng thái trung gian (context lớn)
    lookup_graph = build_graph(deps, False)
    manager, cfg = runtime.manager, runtime.cfg
    gate_llm = deps.llm_main if cfg["gate"].get("llm") == "main" else deps.llm_fast

    def emit(event: str, data: Any) -> None:
        get_stream_writer()({"event": event, "data": data})

    def gate_node(state: AssistantState) -> dict:
        emit("status", {"step": "gate"})
        out = run_gate(gate_llm, cfg["gate"], state["question"], state.get("messages") or [],
                       state.get("attachments"), state.get("force_route"))
        emit("route", {"route": out.route})
        return {"route": out.route, "gate_reason": out.reason, "status": None, "answer": "", "citations": [],
                "files": [], "stop_reason": None, "output_before": {}}

    def lookup_node(state: AssistantState) -> dict:
        inp = {k: state.get(k) for k in ("conversation_id", "user_id", "question", "messages", "active_projects")}
        values: dict[str, Any] = {}
        for mode, chunk in lookup_graph.stream(inp, stream_mode=["custom", "values"]):
            if mode == "custom":
                get_stream_writer()(chunk)
            else:
                values = chunk
        return {k: values.get(k) for k in ("messages", "active_projects", "status", "answer", "citations")}

    def agent_prepare_node(state: AssistantState) -> dict:
        """Tách khỏi node agent: chụp danh sách file đúng 1 lần, không chụp lại khi resume sau xác nhận."""
        emit("status", {"step": "agent"})
        try:
            return {"output_before": output_snapshot(manager, state["conversation_id"])}
        except Exception as e:  # noqa: BLE001
            log.warning("đọc danh sách file sandbox lỗi: %s", e)
            return {"output_before": {}}

    def agent_node(state: AssistantState) -> dict:
        ctx = AgentContext(state["user_id"], state["conversation_id"], list(state.get("active_projects") or []),
                           list(state.get("messages") or []))
        final = stream_agent(runtime.agent, ctx, state["question"], state.get("attachments"),
                             int(cfg["agent"]["max_steps"]), emit)
        res = agent_result(final)
        emit("token", res["answer"])
        status = "PARTIAL" if res["stop_reason"] else "OK"
        return {"answer": res["answer"], "citations": res["citations"], "status": status,
                "stop_reason": res["stop_reason"],
                "active_projects": res["active_projects"] or list(state.get("active_projects") or [])}

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
                *turn_messages(state["question"], state.get("answer") or "", route="agent", status=state.get("status"),
                               citations=state.get("citations"), files=files, attachments=state.get("attachments"))]
        try:
            deps.repo.upsert_conversation(cid, uid, msgs[0]["content"] if msgs else None)
        except Exception as e:  # noqa: BLE001
            log.error("save conversation lỗi: %s", e)
        return {"messages": msgs, "files": files}

    g = StateGraph(AssistantState)
    g.add_node("gate", gate_node)
    g.add_node("lookup", lookup_node)
    g.add_node("agent_prepare", agent_prepare_node)
    g.add_node("agent", agent_node)
    g.add_node("save", save_node)
    g.add_edge(START, "gate")
    g.add_conditional_edges("gate", lambda s: "agent_prepare" if s["route"] == "agent" else "lookup",
                            ["lookup", "agent_prepare"])
    g.add_edge("lookup", END)
    g.add_edge("agent_prepare", "agent")
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
    done = {"conversation_id": conversation_id, "route": st.get("route"),
            "active_projects": st.get("active_projects") or []}
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
                          attachments: list[str] | None = None, force_route: str | None = None) -> Iterator[dict]:
    """Sự kiện: status · route · token · tool_start · tool_end · citations · file · confirm_required · done."""
    cid = conversation_id or uuid.uuid4().hex
    inp = {"conversation_id": cid, "user_id": user_id, "question": question, "attachments": attachments or [],
           "force_route": force_route}
    yield from _stream(graph, inp, cid)


def stream_confirm(graph, conversation_id: str, confirm_id: str, approve: bool) -> Iterator[dict]:
    """Tiếp tục lượt đang chờ xác nhận tool write."""
    yield from _stream(graph, resume_command(confirm_id, approve), conversation_id)


def pending_confirm_ids(graph, conversation_id: str) -> list[str]:
    return [p["confirm_id"] for p in pending_confirms(graph.get_state(turn_config(conversation_id)).interrupts)]

