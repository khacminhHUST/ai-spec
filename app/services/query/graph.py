"""LangGraph Query Pipeline: LOAD → N1 → N2 → (ASK_CLARIFY | N3 fan-out → N4 → N5 → N6 ⇄ Targeted Fetch → N7) → SAVE.

Checkpointer: PostgresSaver (thread_id = conversation_id) – xem `postgres_checkpointer`.
Sự kiện stream (stream_mode="custom"): {"event": "status"|"token", "data": ...}.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Annotated, Any, Iterator, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.services.query.context import build_context
from app.services.query.deps import Deps
from app.services.query.generator import cited_numbers, generate
from app.services.query.grader import grade, only_irrelevant, route_after_grade, targeted_fetch
from app.services.query.resolver import clarify_message, load_profiles, resolve
from app.services.query.retrieval import plan_branches, rerank, run_branch
from app.services.query.router import run_router

log = logging.getLogger(__name__)


def add_or_reset(left: list | None, right: list | None) -> list:
    """Reducer cộng dồn kết quả các nhánh; ghi None = reset (đầu mỗi lượt)."""
    if right is None:
        return []
    return [*(left or []), *right]


class GraphState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    question: str
    messages: list[dict]
    active_projects: list[str]
    project_profiles: dict
    router: dict
    router_fallback: bool
    clarify_options: list[dict]
    branches: list[dict]
    candidates: Annotated[list[dict], add_or_reset]
    reranked: list[dict]
    top1_score: float
    context: list[dict]
    missing: list[str]
    hop_count: int
    extra_parent_ids: list[str]
    status: str | None
    answer: str
    citations: list[dict]


class BranchState(TypedDict):
    branch: dict
    router: dict


TRANSIENT_RESET = {
    "router": {}, "router_fallback": False, "clarify_options": [], "branches": [], "candidates": None,
    "reranked": [], "top1_score": 0.0, "context": [], "missing": [], "hop_count": 0, "extra_parent_ids": [],
    "status": None, "answer": "", "citations": [], "project_profiles": {},
}


def _emit(event: str, data: Any) -> None:
    try:
        get_stream_writer()({"event": event, "data": data})
    except Exception:  # noqa: BLE001 – ngoài ngữ cảnh stream
        pass


def build_graph(deps: Deps, checkpointer=None):
    max_hops = int(deps.cfg("grader").get("max_hops", 1))

    # ---------------------------------------------------------------- nodes
    def load_state(state: GraphState) -> dict:
        _emit("status", {"step": "load"})
        return {**TRANSIENT_RESET, "messages": list(state.get("messages") or []),
                "active_projects": list(state.get("active_projects") or [])}

    def router_node(state: GraphState) -> dict:
        _emit("status", {"step": "router"})
        allowed = deps.repo.user_projects(state["user_id"])
        info = deps.repo.project_info(allowed)
        user_projects = [{"name": info.get(p, {}).get("name", p), "aliases": info.get(p, {}).get("aliases", [])}
                         for p in allowed]
        active_names = [info.get(p, {}).get("name", p) for p in state.get("active_projects") or []]
        out, fb = run_router(deps, state["question"], state.get("messages") or [], active_names, user_projects)
        return {"router": out.model_dump(), "router_fallback": fb}

    def resolver_node(state: GraphState) -> dict:
        _emit("status", {"step": "resolve"})
        r = state["router"]
        res = resolve(deps, state["user_id"], r["project_action"], r["project_mentions"],
                      state.get("active_projects") or [])
        if res.needs_clarify:
            msg, options = clarify_message(deps, state["user_id"], res)
            return {"status": "ASK_CLARIFY", "answer": msg, "clarify_options": options,
                    "active_projects": res.active_projects}
        profiles = load_profiles(deps, res.active_projects)
        return {"active_projects": res.active_projects, "project_profiles": profiles,
                "branches": plan_branches(r, profiles)}

    def route_after_resolve(state: GraphState):
        if state.get("status") == "ASK_CLARIFY":
            return "ask_clarify"
        branches = state.get("branches") or []
        if not branches:
            return "rerank"
        return [Send("retrieve", {"branch": b, "router": state["router"]}) for b in branches]

    def retrieve_node(bstate: BranchState) -> dict:
        return {"candidates": run_branch(deps, bstate["branch"], bstate["router"])}

    def rerank_node(state: GraphState) -> dict:
        _emit("status", {"step": "rerank", "candidates": len(state.get("candidates") or [])})
        kept, top1 = rerank(deps, state["router"]["rewritten_question"], state.get("candidates") or [])
        return {"reranked": kept, "top1_score": top1, "status": None if kept else "NOT_FOUND"}

    def route_after_rerank(state: GraphState) -> str:
        return "generate" if state.get("status") == "NOT_FOUND" else "build_context"

    def context_node(state: GraphState) -> dict:
        _emit("status", {"step": "context"})
        return {"context": build_context(deps, state.get("reranked") or [], state.get("project_profiles") or {},
                                         state.get("extra_parent_ids") or [])}

    def grader_node(state: GraphState) -> dict:
        _emit("status", {"step": "grade"})
        missing = grade(deps, state["router"]["rewritten_question"], state.get("context") or [],
                        float(state.get("top1_score") or 0.0))
        decision = route_after_grade(missing, int(state.get("hop_count") or 0), max_hops)
        return {"missing": missing, "status": None if decision == "FETCH" else decision}

    def route_after_grader(state: GraphState) -> str:
        return "targeted_fetch" if state.get("status") is None else "generate"

    def fetch_node(state: GraphState) -> dict:
        _emit("status", {"step": "targeted_fetch", "missing": state.get("missing") or []})
        return targeted_fetch(deps, state)

    def generate_node(state: GraphState) -> dict:
        status = state.get("status") or "OK"
        _emit("status", {"step": "generate", "status": status})
        answer, cites = generate(
            deps, state["router"]["rewritten_question"], status, state.get("context") or [],
            state.get("project_profiles") or {}, state.get("missing") or [], on_token=lambda t: _emit("token", t),
        )
        if status == "PARTIAL" and only_irrelevant(state.get("missing") or [])                 and not cited_numbers(answer, state.get("context") or []):
            # Grader: context không liên quan, và N7 không trích dẫn được nguồn nào → không có trong tài liệu.
            status, cites = "NOT_FOUND", []
        return {"answer": answer, "citations": cites, "status": status}

    def ask_clarify_node(state: GraphState) -> dict:
        _emit("status", {"step": "ask_clarify", "status": "ASK_CLARIFY"})
        _emit("token", state.get("answer") or "")
        return {}

    def save_state(state: GraphState) -> dict:
        _emit("status", {"step": "save"})
        msgs = list(state.get("messages") or [])
        msgs.append({"role": "user", "content": state["question"]})
        msgs.append({"role": "assistant", "content": state.get("answer") or "", "status": state.get("status"),
                     "citations": state.get("citations") or [],
                     "rewritten_question": (state.get("router") or {}).get("rewritten_question")})
        if state.get("conversation_id"):
            try:
                deps.repo.upsert_conversation(state["conversation_id"], state["user_id"],
                                              msgs[0]["content"] if msgs else None)
            except Exception as e:  # noqa: BLE001
                log.error("save conversation lỗi: %s", e)
        return {"messages": msgs, "active_projects": state.get("active_projects") or []}

    # ---------------------------------------------------------------- wiring
    g = StateGraph(GraphState)
    g.add_node("load", load_state)
    g.add_node("router", router_node)
    g.add_node("resolve", resolver_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("rerank", rerank_node)
    g.add_node("build_context", context_node)
    g.add_node("grade", grader_node)
    g.add_node("targeted_fetch", fetch_node)
    g.add_node("generate", generate_node)
    g.add_node("ask_clarify", ask_clarify_node)
    g.add_node("save", save_state)

    g.add_edge(START, "load")
    g.add_edge("load", "router")
    g.add_edge("router", "resolve")
    g.add_conditional_edges("resolve", route_after_resolve, ["ask_clarify", "rerank", "retrieve"])
    g.add_edge("retrieve", "rerank")
    g.add_conditional_edges("rerank", route_after_rerank, ["generate", "build_context"])
    g.add_edge("build_context", "grade")
    g.add_conditional_edges("grade", route_after_grader, ["targeted_fetch", "generate"])
    g.add_edge("targeted_fetch", "build_context")
    g.add_edge("generate", "save")
    g.add_edge("ask_clarify", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=checkpointer)


# -------------------------------------------------------------------- chạy

def turn_config(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": conversation_id}, "recursion_limit": 50}


def run_turn(graph, user_id: str, question: str, conversation_id: str | None = None) -> dict:
    cid = conversation_id or uuid.uuid4().hex
    out = graph.invoke({"conversation_id": cid, "user_id": user_id, "question": question}, turn_config(cid))
    return {**out, "conversation_id": cid}


def stream_turn(graph, user_id: str, question: str, conversation_id: str | None = None) -> Iterator[dict]:
    """Sự kiện: status · token · citations · done."""
    cid = conversation_id or uuid.uuid4().hex
    cfg = turn_config(cid)
    for ev in graph.stream({"conversation_id": cid, "user_id": user_id, "question": question}, cfg,
                           stream_mode="custom"):
        yield ev
    st = graph.get_state(cfg).values
    yield {"event": "citations", "data": st.get("citations") or []}
    yield {"event": "done", "data": {"conversation_id": cid, "status": st.get("status"),
                                      "active_projects": st.get("active_projects") or []}}


@contextmanager
def postgres_checkpointer(settings=None):
    """PostgresSaver trên kết nối `app.db.client.checkpointer_conn` (bảng đã tạo bởi app.db.migrate)."""
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.db.client import checkpointer_conn

    with checkpointer_conn(settings) as conn:
        yield PostgresSaver(conn)
