"""N7. Generator – Main LLM, streaming. Context XML project → stream → section (vi/en, trang, rev).

LLM chỉ trích dẫn bằng số thứ tự khối [n]; code ánh xạ [n] → citation (doc_id, section_key, trang, lang).
"""
from __future__ import annotations

import re
from typing import Any, Callable
from xml.sax.saxutils import escape, quoteattr

from langchain_core.messages import HumanMessage, SystemMessage

from app.models.query import Citation
from app.prompts import load, render
from app.services.query.deps import Deps, message_text

SYSTEM = load("query/generator_system")

_CITE_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def _pages(b: dict[str, Any]) -> str:
    s, e = b.get("page_start"), b.get("page_end")
    if s is None:
        return ""
    return str(s) if e in (None, s) else f"{s}-{e}"


def build_context_xml(context: list[dict[str, Any]], profiles: dict[str, Any]) -> str:
    if not context:
        return "<context/>"
    out = ["<context>"]
    by_proj: dict[str, list[dict[str, Any]]] = {}
    for b in context:
        by_proj.setdefault(b.get("project_id") or "", []).append(b)
    for pid, blocks in by_proj.items():
        prof = profiles.get(pid, {})
        out.append(f"<project id={quoteattr(pid)} name={quoteattr(prof.get('name', pid))} "
                   f"aliases={quoteattr(', '.join(prof.get('aliases') or []))} "
                   f"governing_language={quoteattr(prof.get('governing_language', 'vi'))}>")
        by_stream: dict[str, list[dict[str, Any]]] = {}
        for b in blocks:
            by_stream.setdefault(b.get("stream") or "", []).append(b)
        for st, sblocks in sorted(by_stream.items(), key=lambda kv: kv[1][0].get("priority", 99)):
            out.append(f"  <stream name={quoteattr(st)} priority=\"{sblocks[0].get('priority', 99)}\">")
            for b in sblocks:
                attrs = {
                    "n": str(b["n"]), "doc": b.get("doc_title") or b["doc_id"], "rev": b.get("revision") or "",
                    "key": b["section_key"], "number": b.get("number") or "", "title": b.get("title") or "",
                    "pages": _pages(b),
                }
                if b.get("is_reference"):
                    attrs["reference_of"] = b.get("referenced_by") or ""
                if b.get("unresolved_refs"):
                    attrs["not_in_system"] = ", ".join(b["unresolved_refs"])
                out.append("    <section " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items()) + ">")
                for lang in ("vi", "en"):
                    if b.get(lang):
                        out.append(f"      <{lang}>{escape(b[lang])}</{lang}>")
                out.append("    </section>")
            out.append("  </stream>")
        out.append("</project>")
    out.append("</context>")
    return "\n".join(out)


def build_messages(question: str, status: str, context: list[dict[str, Any]], profiles: dict[str, Any],
                   missing: list[str] | None = None) -> list:
    note = {
        "OK": "OK",
        "PARTIAL": "PARTIAL – context chưa đủ" + (f" (thiếu: {', '.join(m.split(':', 1)[-1] for m in missing)})" if missing else ""),
        "NOT_FOUND": "NOT_FOUND – không có tài liệu liên quan",
    }.get(status, status)
    human = render("query/generator_human", context=build_context_xml(context, profiles), status=note,
                   question=question)
    return [SystemMessage(SYSTEM), HumanMessage(human)]


def cited_numbers(answer: str, context: list[dict[str, Any]]) -> list[int]:
    """Các số [n] hợp lệ LLM đã trích dẫn (theo thứ tự xuất hiện)."""
    valid = {b["n"] for b in context}
    ns: list[int] = []
    for m in _CITE_RE.finditer(answer):
        for x in re.split(r"[,;]", m.group(1)):
            n = int(x.strip())
            if n in valid and n not in ns:
                ns.append(n)
    return ns


def extract_citations(answer: str, context: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    if status == "NOT_FOUND" or not context:
        return []
    by_n = {b["n"]: b for b in context}
    ns = cited_numbers(answer, context)
    if not ns:  # LLM quên trích dẫn → gắn khối chính điểm cao nhất
        ns = [b["n"] for b in context if not b.get("is_reference")][:2] or [context[0]["n"]]
    cites = []
    for n in ns:
        b = by_n[n]
        cites.append(Citation(
            n=n, doc_id=b["doc_id"], doc_title=b.get("doc_title"), revision=b.get("revision"),
            section_key=b["section_key"], number=b.get("number"), title=b.get("title"),
            page=b.get("page_start"), page_end=b.get("page_end"),
            lang=[lang for lang in ("vi", "en") if b.get(lang)],
        ).model_dump())
    return cites


def generate(deps: Deps, question: str, status: str, context: list[dict[str, Any]], profiles: dict[str, Any],
             missing: list[str] | None = None, on_token: Callable[[str], None] | None = None) -> tuple[str, list]:
    msgs = build_messages(question, status, context, profiles, missing)
    parts: list[str] = []
    for chunk in deps.llm_main.stream(msgs):
        t = message_text(chunk)
        if t:
            parts.append(t)
            if on_token:
                on_token(t)
    answer = "".join(parts).strip()
    return answer, extract_citations(answer, context, status)
