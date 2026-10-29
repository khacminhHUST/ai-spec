"""search_project_docs: bọc Query Pipeline N1→N6 (router → resolver → retrieval → rerank → context → grader), KHÔNG chạy N7.

Tái sử dụng nguyên graph của Query Pipeline (build_graph) với interrupt_before generate/ask_clarify trên checkpointer
in-memory tạm (mỗi lần gọi 1 thread, xóa sau khi xong) – không sửa app/services/query.
user_id / active_projects / lịch sử lấy từ AgentContext; quyền dự án do resolver (N2) ép theo user_id.
Mỗi khối context được đánh số toàn cục trong lượt agent (state `sources`) để câu trả lời cuối trích dẫn [n].
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.models.query import Citation, RouterOutput
from app.services.agent.loop.context import fit_or_offload
from app.services.agent.sandbox import SandboxManager
from app.services.agent.state import AgentContext, runtime_context
from app.services.agent.tools.base import clip, fail, ok
from app.services.query.deps import Deps
from app.services.query.graph import build_graph

log = logging.getLogger(__name__)

DESC = """Tra cứu tài liệu dự án (thuyết minh, spec, chỉ dẫn kỹ thuật, tiêu chuẩn vận hành/Westin, TCVN/QCVN, hợp đồng) của các dự án người dùng có quyền.
Dùng khi: cần số liệu/yêu cầu kỹ thuật để trả lời hoặc để đưa vào file. MỌI số liệu đưa vào câu trả lời/file phải lấy từ kết quả tool này (hoặc file người dùng gửi).
- question: câu hỏi tra cứu đầy đủ, cụ thể, 1 chủ đề/lần (VD "Bảng 4.3 lưu lượng gió tươi theo khu vực trong Thuyết minh S5"). Cần nhiều chủ đề/nhiều nguồn → gọi nhiều lần.
- project_mentions: tên/mã dự án hoặc công trình người dùng nhắc (VD ["S5"]); không nhắc → bỏ trống (dùng dự án đang chọn).
- disciplines: lọc phân hệ trong HVAC, ELECTRICAL, ELV, PLUMBING, FIRE, LIFT, BMS, GENERAL (không chắc → bỏ trống).
- streams: lọc loại nguồn LEGAL_STANDARD (TCVN/QCVN), OPERATOR_STANDARD (Westin/Marriott), DESIGN_CONSULTANT (thuyết minh/spec/chỉ dẫn), CONTRACT (hợp đồng). Chỉ điền khi cần đúng 1 loại nguồn (VD so sánh Thuyết minh với Westin → gọi 2 lần, mỗi lần 1 stream).
Trả về JSON: status (OK | PARTIAL | NOT_FOUND | ASK_CLARIFY), blocks (n, doc, rev, section, pages, lang, vi/en = nội dung), citations.
- Trích dẫn trong câu trả lời bằng [n] của block. NOT_FOUND → nói rõ không có trong tài liệu, KHÔNG tự điền số liệu.
- ASK_CLARIFY → hỏi lại người dùng theo clarify_options rồi dừng.
- Kết quả dài → full_result_file (JSON trong /workspace/input) để đọc đủ số liệu."""

SUMMARY_BLOCK_CHARS = 400


def add_sources(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Reducer state `sources`: cộng dồn citation (n toàn cục); right = None → reset (đầu lượt)."""
    if right is None:
        return []
    return [*(left or []), *right]


def apply_hints(router: dict[str, Any], project_mentions: list[str] | None, disciplines: list[str] | None,
                streams: list[str] | None) -> dict[str, Any]:
    """Gợi ý của agent ghi đè/bổ sung output N1 (giá trị ngoài enum bị loại bởi RouterOutput)."""
    r = dict(router)
    if project_mentions:
        if not r.get("project_mentions") and r.get("project_action") == "keep":
            r["project_action"] = "set"
        r["project_mentions"] = [*(r.get("project_mentions") or []), *project_mentions]
    if disciplines:
        r["disciplines"] = disciplines
    if streams:
        r["streams"] = streams
    return RouterOutput(**r).model_dump()


def _pages(b: dict[str, Any]) -> str:
    s, e = b.get("page_start"), b.get("page_end")
    return "" if s is None else (str(s) if e in (None, s) else f"{s}-{e}")


def _block(b: dict[str, Any], n: int, profiles: dict[str, Any]) -> dict[str, Any]:
    out = {"n": n, "project": profiles.get(b.get("project_id"), {}).get("name", b.get("project_id")),
           "stream": b.get("stream"), "doc": b.get("doc_title") or b["doc_id"], "rev": b.get("revision"),
           "section": " ".join(x for x in (b.get("number"), b.get("title")) if x) or b["section_key"],
           "pages": _pages(b), "lang": [lang for lang in ("vi", "en") if b.get(lang)]}
    for lang in ("vi", "en"):
        if b.get(lang):
            out[lang] = b[lang]
    if b.get("is_reference"):
        out["reference_of"] = b.get("referenced_by")
    if b.get("unresolved_refs"):
        out["not_in_system"] = b["unresolved_refs"]
    return out


def _citation(b: dict[str, Any], n: int) -> dict[str, Any]:
    return Citation(n=n, doc_id=b["doc_id"], doc_title=b.get("doc_title"), revision=b.get("revision"),
                    section_key=b["section_key"], number=b.get("number"), title=b.get("title"),
                    page=b.get("page_start"), page_end=b.get("page_end"),
                    lang=[lang for lang in ("vi", "en") if b.get(lang)]).model_dump()


def _summarize(payload: dict[str, Any]) -> dict[str, Any]:
    short = {k: v for k, v in payload.items() if k not in ("blocks", "citations")}
    short["blocks"] = [{**{k: v for k, v in b.items() if k not in ("vi", "en")},
                        **{lang: clip(b[lang], SUMMARY_BLOCK_CHARS) for lang in ("vi", "en") if b.get(lang)}}
                       for b in payload.get("blocks", [])]
    return short


class SearchDocs:
    """Chạy N1–N6 cho 1 câu hỏi của agent. Graph dựng 1 lần, mỗi lần gọi 1 thread in-memory riêng."""

    def __init__(self, deps: Deps):
        self.deps = deps
        self.saver = InMemorySaver()
        self.graph = build_graph(deps, self.saver)

    def run(self, ctx: AgentContext, question: str, active_projects: list[str], project_mentions=None,
            disciplines=None, streams=None) -> dict[str, Any]:
        tid = f"agent-search-{uuid.uuid4().hex}"
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 50}
        inp = {"user_id": ctx.user_id, "question": question, "messages": list(ctx.history),
               "active_projects": list(active_projects)}
        try:
            self.graph.invoke(inp, cfg, interrupt_after=["router"])
            if project_mentions or disciplines or streams:
                router = self.graph.get_state(cfg).values["router"]
                self.graph.update_state(cfg, {"router": apply_hints(router, project_mentions, disciplines, streams)},
                                        as_node="router")
            self.graph.invoke(None, cfg, interrupt_before=["generate", "ask_clarify"])
            return dict(self.graph.get_state(cfg).values)
        finally:
            self.saver.delete_thread(tid)


def search_impl(search: SearchDocs, manager: SandboxManager, agent_cfg: dict[str, Any], ctx: AgentContext,
                state: dict[str, Any], question: str, project_mentions=None, disciplines=None, streams=None
                ) -> tuple[str, list[dict], list[str]]:
    """→ (JSON cho LLM, citations mới (n toàn cục), active_projects sau tra cứu)."""
    active = list(state.get("active_projects") or ctx.active_projects)
    try:
        st = search.run(ctx, question, active, project_mentions, disciplines, streams)
    except Exception as e:  # noqa: BLE001
        log.exception("search_project_docs lỗi")
        return fail(f"Tra cứu lỗi: {e}"), [], active
    status = st.get("status") or "OK"
    active = list(st.get("active_projects") or active)
    profiles = st.get("project_profiles") or {}
    if status == "ASK_CLARIFY":
        return ok(status=status, message=st.get("answer") or "", clarify_options=st.get("clarify_options") or []
                  ), [], active
    start = len(state.get("sources") or [])
    context = (st.get("context") or []) if status != "NOT_FOUND" else []
    blocks = [_block(b, start + i + 1, profiles) for i, b in enumerate(context)]
    cites = [_citation(b, start + i + 1) for i, b in enumerate(context)]
    payload = {"status": "NOT_FOUND" if not blocks else status,
               "question": (st.get("router") or {}).get("rewritten_question") or question,
               "projects": [p.get("name", pid) for pid, p in profiles.items()], "blocks": blocks, "citations": cites}
    if status == "PARTIAL":
        payload["missing"] = [m.split(":", 1)[-1] for m in st.get("missing") or []]
    if not blocks:
        payload["message"] = "Không tìm thấy thông tin trong tài liệu dự án – không được tự điền số liệu."
    try:
        payload, _ = fit_or_offload(manager, ctx.conversation_id, "search", payload, _summarize,
                                    int(agent_cfg["tool_result_max_tokens"]), search.deps.count_tokens)
    except Exception as e:  # noqa: BLE001 – sandbox lỗi: vẫn trả bản tóm tắt
        log.warning("offload kết quả search lỗi: %s", e)
        payload = {**_summarize(payload), "note": "Kết quả bị cắt (không ghi được file sandbox)."}
    return ok(**payload), cites, active


def make_search_tool(search: SearchDocs, manager: SandboxManager, agent_cfg: dict[str, Any]) -> BaseTool:
    """agent_cfg = agent.yaml › agent."""

    @tool("search_project_docs", description=DESC)
    def search_project_docs(question: str, runtime: ToolRuntime, project_mentions: list[str] | None = None,
                            disciplines: list[str] | None = None, streams: list[str] | None = None) -> Command:
        ctx = runtime_context(runtime)
        text, cites, active = search_impl(search, manager, agent_cfg, ctx, dict(runtime.state or {}), question,
                                          project_mentions, disciplines, streams)
        msg = ToolMessage(text, tool_call_id=runtime.tool_call_id, name="search_project_docs")
        return Command(update={"messages": [msg], "sources": cites, "active_projects": active})

    return search_project_docs
