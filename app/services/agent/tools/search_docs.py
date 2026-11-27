"""search_project_docs: bọc Query Pipeline N1→N6 (router → resolver → retrieval → rerank → context → grader), KHÔNG chạy N7.

Tái sử dụng nguyên graph của Query Pipeline (build_graph) với interrupt_before generate/ask_clarify trên checkpointer
in-memory tạm (mỗi lần gọi 1 thread, xóa sau khi xong) – không sửa app/services/query.
user_id / active_projects / lịch sử lấy từ AgentContext; quyền dự án do resolver (N2) ép theo user_id.
Mỗi khối context được đánh số toàn cục trong lượt agent (state `sources`) để câu trả lời cuối trích dẫn [n].
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

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
from app.services.query.context import drop_heading_only
from app.services.query.deps import Deps
from app.services.query.graph import build_graph

log = logging.getLogger(__name__)

DESC = """Tra cứu tài liệu dự án (thuyết minh, spec, chỉ dẫn kỹ thuật, tiêu chuẩn vận hành/Westin, TCVN/QCVN, hợp đồng) của các dự án người dùng có quyền.
Dùng khi: cần số liệu/yêu cầu kỹ thuật để trả lời hoặc để đưa vào file. MỌI số liệu đưa vào câu trả lời/file phải lấy từ kết quả tool này (hoặc file người dùng gửi).
- question: câu hỏi tra cứu đầy đủ, cụ thể, 1 chủ đề/lần (VD "Bảng 4.3 lưu lượng gió tươi theo khu vực trong Thuyết minh S5"). Cần nhiều chủ đề/nhiều nguồn → gọi nhiều lần CÙNG LÚC trong 1 bước (chạy song song); số bước tra mỗi câu hỏi có giới hạn.
- project_mentions: tên/mã dự án hoặc công trình người dùng nhắc (VD ["S5"]); không nhắc → bỏ trống (dùng dự án đang chọn).
- disciplines: lọc phân hệ trong HVAC, ELECTRICAL, ELV, PLUMBING, FIRE, LIFT, BMS, GENERAL (không chắc → bỏ trống).
- streams: lọc loại nguồn LEGAL_STANDARD (TCVN/QCVN), OPERATOR_STANDARD (Westin/Marriott), DESIGN_CONSULTANT (thuyết minh/spec/chỉ dẫn), CONTRACT (hợp đồng). Chỉ điền khi cần đúng 1 loại nguồn (VD so sánh Thuyết minh với Westin → gọi 2 lần, mỗi lần 1 stream).
Trả về JSON: status (OK | PARTIAL | NOT_FOUND | ASK_CLARIFY), projects (name, aliases, governing_language), blocks (n, stream, priority – 1 là cao nhất, doc, rev, section, pages, lang, vi/en = nội dung), citations.
- Trích dẫn trong câu trả lời bằng [n] của block. NOT_FOUND → nói rõ không có trong tài liệu, KHÔNG tự điền số liệu.
- ASK_CLARIFY → hỏi lại người dùng theo clarify_options rồi dừng.
- Kết quả dài → full_result_file (JSON trong /workspace/input) để đọc đủ số liệu."""

SUMMARY_BLOCK_CHARS = 400
SUMMARY_TABLE_CHARS = 1500     # bảng ngắn giữ nguyên trong bản tóm tắt
SUMMARY_RESERVE_TOKENS = 600   # chừa cho note/full_result_file khi xếp khối đầy đủ vào bản tóm tắt
EMPTY_TABLE_CHARS = 30         # bảng kèm < N chữ/số và không có chữ số → coi là rỗng (VD "| Type | L |")
_SECTION_RE = re.compile(r"^(?P<scope>.*__)?sec_(?P<num>\d+_\d+)$")
_ALNUM_RE = re.compile(r"\w", re.UNICODE)


def near_empty_table(text: str | None) -> bool:
    """Bảng gần như rỗng: tối đa 1 hàng, ngắn, không số liệu – VD "| Type | L |" (thường do cắt cột song ngữ khi
    ingest). Bảng ≥ 2 hàng có chữ (VD bảng màu pha) luôn giữ."""
    if not text:
        return True
    rows = [ln for ln in text.splitlines()
            if ln.strip().startswith("|") and re.sub(r"[|:\-\s]", "", ln) and not re.fullmatch(r"[|:\-\s]+", ln)]
    body = re.sub(r"[|:\-\s]", "", text)
    if not body:
        return True
    return len(rows) <= 1 and not any(c.isdigit() for c in body) and len(_ALNUM_RE.findall(body)) < EMPTY_TABLE_CHARS


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


def expand_sibling_tables(deps: Deps, context: list[dict[str, Any]], max_tables: int) -> list[dict[str, Any]]:
    """Nạp kèm bảng của mục đã trúng (sec_5_7 → table_5_7_1, table_5_7_2…), đặt ngay sau khối mục.

    Bảng tách thành khối riêng có thể không có hàng tiêu đề (chữ mô tả nằm ở khối mục) nên tìm theo nội dung không
    trúng, trong khi khối mục chỉ còn đầu mục "…:" không số liệu → agent dễ kết luận sai là "không có trong tài liệu".
    """
    wanted: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for b in context:
        m = _SECTION_RE.match(b["section_key"])
        if m and not b.get("is_reference"):
            for i in range(1, max_tables + 1):
                wanted.setdefault((b["doc_id"], f"{m['scope'] or ''}table_{m['num']}_{i}"), (b, f"table_{m['num']}_{i}"))
    loaded = {(b["doc_id"], b["section_key"]) for b in context}
    missing = {k: v for k, v in wanted.items() if k not in loaded}
    if not missing:
        return context
    rows = deps.repo.find_sections(sorted({d for d, _ in missing}), sorted({ref for _, ref in missing.values()}))
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["doc_id"], r["section_key"])
        if key in missing:
            parent = missing[key][0]
            t = tables.setdefault(key, {
                **{k: parent.get(k) for k in ("project_id", "stream", "priority", "doc_id", "doc_title", "revision")},
                "section_key": r["section_key"], "number": r.get("number"), "title": r.get("title"),
                "page_start": r.get("page_start"), "page_end": r.get("page_end"), "is_reference": True,
                "referenced_by": parent["section_key"]})
            t[r["lang"]] = r["content_md"]
    tables = {k: t for k, t in tables.items() if not all(near_empty_table(t.get(lang)) for lang in ("vi", "en"))}
    out: list[dict[str, Any]] = []
    for b in context:
        out.append(b)
        out += [t for k, t in sorted(tables.items()) if t["referenced_by"] == b["section_key"] and k[0] == b["doc_id"]]
    return out


def _pages(b: dict[str, Any]) -> str:
    s, e = b.get("page_start"), b.get("page_end")
    return "" if s is None else (str(s) if e in (None, s) else f"{s}-{e}")


def _block(b: dict[str, Any], n: int, profiles: dict[str, Any]) -> dict[str, Any]:
    out = {"n": n, "project": profiles.get(b.get("project_id"), {}).get("name", b.get("project_id")),
           "stream": b.get("stream"), "priority": b.get("priority"), "doc": b.get("doc_title") or b["doc_id"],
           "rev": b.get("revision"),
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


def _summary_block(b: dict[str, Any]) -> dict[str, Any]:
    short = {k: v for k, v in b.items() if k not in ("vi", "en")}
    for lang in ("vi", "en"):
        text = b.get(lang)
        if text:
            limit = SUMMARY_TABLE_CHARS if text.lstrip().startswith("|") else SUMMARY_BLOCK_CHARS
            short[lang] = clip(text, limit)
            if len(text) > limit:
                short["truncated"] = True
    return short


def summarize_payload(payload: dict[str, Any], max_tokens: int | None = None,
                      count_tokens: Callable[[str], int] | None = None) -> dict[str, Any]:
    """Bản ngắn, đánh dấu truncated ở khối bị cắt.

    Có max_tokens: duyệt theo thứ tự (đã theo điểm rerank), khối nào còn vừa ngân sách thì giữ NGUYÊN, không vừa mới cắt –
    để agent trả lời được ngay mà không phải đọc file (mỗi lần đọc là 1 vòng LLM gửi lại toàn bộ ngữ cảnh).
    Không có max_tokens: cắt đều mọi khối (bảng ngắn giữ nguyên).
    """
    short = {k: v for k, v in payload.items() if k not in ("blocks", "citations")}
    blocks = payload.get("blocks", [])
    if not max_tokens or count_tokens is None:
        short["blocks"] = [_summary_block(b) for b in blocks]
        return short
    dump = lambda o: json.dumps(o, ensure_ascii=False, default=str)  # noqa: E731
    cut = [_summary_block(b) for b in blocks]
    left = max_tokens - SUMMARY_RESERVE_TOKENS - count_tokens(dump(short)) - sum(count_tokens(dump(c)) for c in cut)
    out = []
    for b, c in zip(blocks, cut):
        if c.get("truncated"):
            extra = count_tokens(dump(b)) - count_tokens(dump(c))
            if extra <= left:
                left -= extra
                c = b
        out.append(c)
    short["blocks"] = out
    return short


class SearchDocs:
    """Chạy N1–N6 cho 1 câu hỏi của agent. Graph dựng 1 lần, mỗi lần gọi 1 thread in-memory riêng."""

    def __init__(self, deps: Deps):
        self.deps = deps
        self.saver = InMemorySaver()
        self.graph = build_graph(deps, self.saver)
        self.pool = ThreadPoolExecutor(thread_name_prefix="agent-search")

    def run(self, ctx: AgentContext, question: str, active_projects: list[str], project_mentions=None,
            disciplines=None, streams=None) -> dict[str, Any]:
        """Chạy ở thread riêng (không kế thừa contextvars): tách khỏi config/stream của graph ngoài, để interrupt
        của N1–N6 không lan ra vòng lặp agent và sự kiện status/token không rò ra SSE."""
        return self.pool.submit(self._run, ctx, question, active_projects, project_mentions, disciplines,
                                streams).result()

    def _run(self, ctx: AgentContext, question: str, active_projects: list[str], project_mentions=None,
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


def search_payload(search: SearchDocs, ctx: AgentContext, active: list[str], question: str, project_mentions=None,
                   disciplines=None, streams=None, *, sibling_tables_max: int = 0, existing: int = 0
                   ) -> tuple[dict[str, Any], list[dict], list[str]]:
    """N1–N6 → (payload JSON: status, question, projects, blocks, citations…; citations; active_projects sau tra cứu).

    Dùng chung cho agent (search_impl) và MCP server cho Claude (app/services/mcp_server). Lỗi tra cứu → raise.
    existing = số nguồn đã có trong state (đánh số [n] tiếp theo).
    """
    st = search.run(ctx, question, active, project_mentions, disciplines, streams)
    status = st.get("status") or "OK"
    active = list(st.get("active_projects") or active)
    profiles = st.get("project_profiles") or {}
    if status == "ASK_CLARIFY":
        return {"status": status, "message": st.get("answer") or "",
                "clarify_options": st.get("clarify_options") or []}, [], active
    context = (st.get("context") or []) if status != "NOT_FOUND" else []
    try:
        context = expand_sibling_tables(search.deps, context, sibling_tables_max)
    except Exception as e:  # noqa: BLE001 – phần bổ sung: lỗi thì vẫn trả kết quả chính
        log.warning("nạp bảng của mục lỗi: %s", e)
    # khối chính chỉ còn tiêu đề (không bảng/tham chiếu kèm) → bỏ; xét SAU khi nạp bảng của mục
    context = drop_heading_only(context, int(search.deps.cfg("context").get("drop_heading_only_chars", 0)))
    first = ctx.allocate_sources(len(context), existing)
    blocks = [_block(b, first + i, profiles) for i, b in enumerate(context)]
    cites = [_citation(b, first + i) for i, b in enumerate(context)]
    payload = {"status": "NOT_FOUND" if not blocks else status,
               "question": (st.get("router") or {}).get("rewritten_question") or question,
               "projects": [{"name": p.get("name", pid), "aliases": p.get("aliases") or [],
                             "governing_language": p.get("governing_language", "vi")} for pid, p in profiles.items()],
               "blocks": blocks, "citations": cites}
    if status == "PARTIAL":
        payload["missing"] = [m.split(":", 1)[-1] for m in st.get("missing") or []]
    if not blocks:
        payload["message"] = "Không tìm thấy thông tin trong tài liệu dự án – không được tự điền số liệu."
    return payload, cites, active


def search_impl(search: SearchDocs, manager: SandboxManager, agent_cfg: dict[str, Any], ctx: AgentContext,
                state: dict[str, Any], question: str, project_mentions=None, disciplines=None, streams=None
                ) -> tuple[str, list[dict], list[str]]:
    """→ (JSON cho LLM, citations mới (n toàn cục), active_projects sau tra cứu)."""
    active = list(state.get("active_projects") or ctx.active_projects)
    try:
        payload, cites, active = search_payload(
            search, ctx, active, question, project_mentions, disciplines, streams,
            sibling_tables_max=int(agent_cfg.get("sibling_tables_max", 0)), existing=len(state.get("sources") or []))
    except Exception as e:  # noqa: BLE001
        log.exception("search_project_docs lỗi")
        return fail(f"Tra cứu lỗi: {e}"), [], active
    if payload["status"] == "ASK_CLARIFY":
        return ok(**payload), [], active
    try:
        max_tok, count = int(agent_cfg["tool_result_max_tokens"]), search.deps.count_tokens
        payload, _ = fit_or_offload(manager, ctx.conversation_id, "search", payload,
                                    lambda p: summarize_payload(p, max_tok, count), max_tok, count)
    except Exception as e:  # noqa: BLE001 – sandbox lỗi: vẫn trả bản tóm tắt
        log.warning("offload kết quả search lỗi: %s", e)
        payload = {**summarize_payload(payload), "note": "Kết quả bị cắt (không ghi được file sandbox)."}
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
