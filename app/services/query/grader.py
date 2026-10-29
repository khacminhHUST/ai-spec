"""N6. Grader + Targeted Fetch.

Check 1 (regex): mã tham chiếu trong khối chính chưa có nội dung trong context nhưng CÓ trong hệ thống → missing.
Check 2 (Fast LLM): chỉ khi top1_score < grader.llm_check_below → đủ thông tin chưa? → missing_query.
Rẽ nhánh: không thiếu → OK · thiếu & hop_count ≥ max_hops → PARTIAL · thiếu & hop_count = 0 → Targeted Fetch.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts import load, render
from app.repositories.postgres import section_matches
from app.services.query.deps import Deps, message_text, parse_json_object
from app.services.query.retrieval import rerank, run_branch
from app.utils.keys import extract_references, is_internal_ref

log = logging.getLogger(__name__)
QUERY_PREFIX = "query:"
NONE_MARK = "none:"   # LLM grader: context không liên quan (N7 hạ PARTIAL → NOT_FOUND nếu câu trả lời không trích dẫn)

GRADER_SYSTEM = load("query/grader_system")


def regex_missing(deps: Deps, context: list[dict[str, Any]]) -> list[str]:
    present = [(b["doc_id"], b["section_key"]) for b in context]
    primary = [b for b in context if not b.get("is_reference")]
    unresolved = {r for b in context for r in b.get("unresolved_refs") or []}
    missing: list[str] = []
    internal_by_doc: dict[str, list[str]] = {}
    standards: list[str] = []
    for b in primary:
        for r in extract_references("\n".join(x for x in (b.get("vi"), b.get("en")) if x)):
            if r in unresolved:
                continue
            if is_internal_ref(r):
                if not any(d == b["doc_id"] and section_matches(sk, r) for d, sk in present):
                    internal_by_doc.setdefault(b["doc_id"], []).append(r)
            else:
                standards.append(r)
    for doc_id, refs in internal_by_doc.items():
        for row in deps.repo.find_sections([doc_id], sorted(set(refs))):
            ref = next(r for r in refs if section_matches(row["section_key"], r))
            if ref not in missing:
                missing.append(ref)
    if standards:
        loaded_docs = {b["doc_id"] for b in context}
        for code, doc_ids in deps.repo.standards_in_system(sorted(set(standards))).items():
            if not loaded_docs & set(doc_ids) and code not in missing:
                missing.append(code)
    return missing


def llm_check(deps: Deps, question: str, context: list[dict[str, Any]]) -> tuple[str, str]:
    """→ (verdict, missing_query). Lỗi → coi như ENOUGH (không chặn trả lời)."""
    ctx = "\n\n".join(f"[{b['n']}] {b.get('title') or b['section_key']}\n{(b.get('vi') or b.get('en') or '')[:3000]}"
                      for b in context)
    msgs = [SystemMessage(GRADER_SYSTEM),
            HumanMessage(render("query/grader_human", question=question, context=ctx or "(rỗng)"))]
    try:
        obj = parse_json_object(message_text(deps.llm_fast.invoke(msgs)))
        verdict = str(obj.get("verdict", "ENOUGH")).upper()
        return (verdict if verdict in ("ENOUGH", "PARTIAL", "NONE") else "ENOUGH"), str(obj.get("missing_query") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("grader LLM lỗi: %s", e)
        return "ENOUGH", ""


def grade(deps: Deps, question: str, context: list[dict[str, Any]], top1: float) -> list[str]:
    missing = regex_missing(deps, context)
    if top1 < float(deps.cfg("grader").get("llm_check_below", 0.5)):
        verdict, mq = llm_check(deps, question, context)
        if verdict != "ENOUGH":
            missing.append((NONE_MARK if verdict == "NONE" else QUERY_PREFIX) + (mq or question))
    return missing


def route_after_grade(missing: list[str], hop_count: int, max_hops: int) -> str:
    """D: đủ → 'OK' · E: thiếu & đã fetch → 'PARTIAL' · F: thiếu & chưa fetch → 'FETCH'."""
    if not missing:
        return "OK"
    return "PARTIAL" if hop_count >= max_hops else "FETCH"


def only_irrelevant(missing: list[str]) -> bool:
    """Grader LLM đánh giá context không liên quan (không có thiếu hụt nào khác)."""
    return bool(missing) and all(m.startswith(NONE_MARK) for m in missing)


def targeted_fetch(deps: Deps, state: dict[str, Any]) -> dict[str, Any]:
    """Có ID → SQL lấy thẳng parent · không ID → search lại missing_query (chỉ project/stream hiện tại)."""
    missing = state.get("missing") or []
    profiles = state.get("project_profiles") or {}
    router = state.get("router") or {}
    ids = [m for m in missing if not m.startswith((QUERY_PREFIX, NONE_MARK))]
    queries = [m.split(":", 1)[1] for m in missing if m.startswith((QUERY_PREFIX, NONE_MARK))]
    all_docs = sorted({d for p in profiles.values() for d in p.get("docs", {})})

    extra_ids = list(state.get("extra_parent_ids") or [])
    internal = [i for i in ids if is_internal_ref(i)]
    if internal:
        ctx_docs = sorted({b["doc_id"] for b in state.get("context") or []}) or all_docs
        extra_ids += [r["parent_id"] for r in deps.repo.find_sections(ctx_docs, internal)]
    std = [i for i in ids if not is_internal_ref(i)]
    std_docs = sorted({d for ds in deps.repo.standards_in_system(std).values() for d in ds}) if std else []

    new_hits: list[dict[str, Any]] = []
    branches = state.get("branches") or []
    if queries or std_docs:
        q = queries or [router.get("rewritten_question") or state.get("question", "")]
        for br in branches:
            new_hits += run_branch(deps, br, router, queries=q, doc_ids=None)
        if std_docs:
            for pid, prof in profiles.items():
                for st, info in prof.get("streams", {}).items():
                    docs = [d for d in info.get("doc_ids", []) if d in std_docs]
                    for lang in info.get("languages", []) if docs else []:
                        new_hits += run_branch(deps, {"project_id": pid, "stream": st, "lang": lang}, router,
                                               queries=[f"{' '.join(std)} {q[0]}"], doc_ids=docs)
    reranked = list(state.get("reranked") or [])
    if new_hits:
        extra, _ = rerank(deps, router.get("rewritten_question") or state.get("question", ""), new_hits)
        seen = {h["child_id"] for h in reranked}
        reranked += [h for h in extra if h["child_id"] not in seen]
    return {"hop_count": int(state.get("hop_count") or 0) + 1, "extra_parent_ids": list(dict.fromkeys(extra_ids)),
            "reranked": reranked}
