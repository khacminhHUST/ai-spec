"""N3. Retrieval fan-out (sinh nhánh project × stream × lang) và N4. Rerank theo từng tài liệu."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from query.deps import Deps
from query.models import STREAMS, ChildHit

log = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="retr")


def plan_branches(router: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Nhánh = project × stream × lang; bỏ nhánh stream không có tài liệu ở lang đó; bỏ en nếu queries_en rỗng.

    Router chỉ định stream nhưng không stream nào có tài liệu → mở rộng ra mọi stream (tránh NOT_FOUND giả).
    """
    streams = router.get("streams") or STREAMS
    langs = ["vi"] + (["en"] if router.get("queries_en") else [])
    if not router.get("queries_vi"):
        langs = [lang for lang in langs if lang != "vi"]
    branches = []
    for pid, prof in profiles.items():
        for st in streams:
            have = (prof.get("streams", {}).get(st) or {}).get("languages", [])
            for lang in langs:
                if lang in have:
                    branches.append({"project_id": pid, "stream": st, "lang": lang})
    if not branches and router.get("streams"):
        return plan_branches({**router, "streams": []}, profiles)
    return branches


def run_branch(deps: Deps, branch: dict[str, str], router: dict[str, Any], *, queries: list[str] | None = None,
               doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """1 nhánh retrieval; lỗi/timeout → [] (ghi log), không làm hỏng request."""
    lang = branch["lang"]
    qs = queries if queries is not None else router.get(f"queries_{lang}") or []
    timeout = float(deps.cfg("retrieval").get("branch_timeout_s", 5))
    fut = _POOL.submit(
        deps.retriever.search, project_id=branch["project_id"], stream=branch["stream"], lang=lang, queries=qs,
        keywords=router.get("exact_keywords") or [], disciplines=router.get("disciplines") or [], doc_ids=doc_ids,
    )
    try:
        hits = fut.result(timeout=timeout)
    except FutureTimeout:
        log.warning("retrieval: nhánh %s timeout %.1fs → bỏ", branch, timeout)
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("retrieval: nhánh %s lỗi %s → bỏ", branch, e)
        return []
    return [ChildHit.model_validate(h).model_dump() for h in hits]


def rerank(deps: Deps, question: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """Chấm (question, child.text) chung VN+EN; mỗi doc giữ score ≥ min_score, tối đa max_per_doc."""
    cfg = deps.cfg("rerank")
    uniq = list({c["child_id"]: c for c in candidates}.values())
    if not uniq:
        return [], 0.0
    try:
        scores = deps.reranker.score(question, [c["text"] for c in uniq])
    except Exception as e:  # noqa: BLE001 – reranker lỗi → dùng thứ tự fusion (score 0 → sẽ bị lọc)
        log.error("rerank lỗi: %s", e)
        scores = [0.0] * len(uniq)
    min_score, max_per_doc = float(cfg.get("min_score", 0.3)), int(cfg.get("max_per_doc", 5))
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for c, s in zip(uniq, scores):
        if s >= min_score:
            by_doc.setdefault(c["doc_id"], []).append({**c, "rerank_score": float(s)})
    kept = []
    for hits in by_doc.values():
        kept += sorted(hits, key=lambda h: -h["rerank_score"])[:max_per_doc]
    kept.sort(key=lambda h: -h["rerank_score"])
    return kept, (kept[0]["rerank_score"] if kept else 0.0)
