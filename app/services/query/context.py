"""N5. Context Builder – SQL.

1. parent_chunks theo (doc_id, section_key) của child trúng – 1 truy vấn, lấy cả VN + EN.
2. Ghép VN + EN cùng doc_id + section_key thành 1 khối (giữ cả hai).
3. referenced_ids → parent được tham chiếu trong các tài liệu đã nạp (1 hop); ngoài hệ thống → nhãn.
4. Parent > max_parent_tokens → header + child trúng ± neighbor_children.
5. Sắp theo điểm rerank; bỏ khối gần trùng nội dung khối đã giữ (dedup_threshold); cắt theo token_budget.
`drop_heading_only` (gọi sau khi nạp bảng của mục ở search_payload): bỏ khối chính chỉ còn tiêu đề.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.models.query import ContextBlock
from app.repositories.postgres import section_matches
from app.services.query.deps import Deps
from app.services.query.resolver import stream_priority
from app.utils.keys import child_seq, is_internal_ref

NOT_IN_SYSTEM = "chưa có trong hệ thống"
log = logging.getLogger(__name__)
_WORD_RE = re.compile(r"\w+", re.UNICODE)
SHINGLE_N = 5
DEDUP_MIN_SHINGLES = 20       # khối quá ngắn (VD bảng 1 dòng) không xét trùng


def _shingles(text: str) -> set[int]:
    w = _WORD_RE.findall(text.lower())
    return {hash(" ".join(w[i:i + SHINGLE_N])) for i in range(len(w) - SHINGLE_N + 1)}


def dedup_blocks(blocks: list[ContextBlock], threshold: float) -> list[ContextBlock]:
    """Bỏ khối mà ≥ threshold nội dung (shingle 5 từ, VN+EN) nằm trong 1 khối đã giữ (khác doc/mục) – VD phần chung
    giữa spec PCCC 3.01 và Cấp thoát nước 2.01. Duyệt theo thứ tự điểm: khối điểm cao giữ; khối sau trùng nhưng
    nguồn ưu tiên hơn (priority nhỏ hơn) thì thay chỗ khối trước. threshold ≤ 0 → giữ nguyên."""
    if threshold <= 0:
        return blocks
    kept: list[tuple[ContextBlock, set[int]]] = []
    for blk in blocks:
        sh = _shingles(blk.text())
        if len(sh) < DEDUP_MIN_SHINGLES:
            kept.append((blk, sh))
            continue
        dup = next((i for i, (k, ksh) in enumerate(kept) if len(ksh) >= DEDUP_MIN_SHINGLES
                    and (k.doc_id, k.section_key) != (blk.doc_id, blk.section_key)
                    and len(sh & ksh) >= threshold * len(sh)), None)
        if dup is None:
            kept.append((blk, sh))
            continue
        k = kept[dup][0]
        if blk.priority < k.priority:
            kept[dup] = (blk.model_copy(update={"score": max(blk.score, k.score)}), sh)
            log.info("context: %s/%s trùng %s/%s – giữ nguồn ưu tiên hơn", k.doc_id, k.section_key, blk.doc_id,
                     blk.section_key)
        else:
            log.info("context: bỏ %s/%s (trùng %s/%s)", blk.doc_id, blk.section_key, k.doc_id, k.section_key)
    return [b for b, _ in kept]


_HEADING_LINE_RE = re.compile(r"^\s*(#+\s|\*\*[^*]+\*\*\s*:?\s*$)")


def heading_only(block: dict[str, Any], min_chars: int) -> bool:
    """Khối chỉ còn tiêu đề: bỏ dòng heading (#…, **…**, dòng trùng số/tên mục) còn < min_chars chữ/số ở mọi ngôn ngữ
    và không có bảng."""
    labels = {_norm(x) for x in (block.get("number"), block.get("title"),
                                 " ".join(x for x in (block.get("number"), block.get("title")) if x)) if x}
    for lang in ("vi", "en"):
        text = block.get(lang) or ""
        if any(ln.lstrip().startswith("|") for ln in text.splitlines()):
            return False
        body = [ln for ln in text.splitlines() if ln.strip() and not _HEADING_LINE_RE.match(ln) and _norm(ln) not in labels]
        if len("".join(_WORD_RE.findall(" ".join(body)))) >= min_chars:
            return False
    return True


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def drop_heading_only(context: list[dict[str, Any]], min_chars: int) -> list[dict[str, Any]]:
    """Bỏ khối chính chỉ còn tiêu đề (heading_only) nếu không kèm khối tham chiếu/bảng (referenced_by = mục này) và
    không có tham chiếu ngoài hệ thống. min_chars ≤ 0 hoặc mọi khối đều bị bỏ → giữ nguyên."""
    if min_chars <= 0:
        return context
    attached = {(b["doc_id"], b.get("referenced_by")) for b in context if b.get("is_reference")}
    out = [b for b in context if b.get("is_reference") or b.get("unresolved_refs")
           or (b["doc_id"], b["section_key"]) in attached or not heading_only(b, min_chars)]
    if not out:          # toàn khối tiêu đề → giữ nguyên (không biến kết quả thành NOT_FOUND)
        return context
    if len(out) < len(context):
        log.info("context: bỏ %d khối chỉ có tiêu đề", len(context) - len(out))
    return out


def _doc_meta(profiles: dict[str, Any], doc_id: str) -> tuple[str | None, dict[str, Any]]:
    for pid, prof in profiles.items():
        if doc_id in prof.get("docs", {}):
            return pid, prof["docs"][doc_id]
    return None, {}


def _trim_parent(deps: Deps, parent: dict[str, Any], hit_seqs: set[int]) -> str:
    """Parent dài → tiêu đề + các child trúng ± neighbor (theo seq), đánh dấu … ở chỗ bỏ."""
    n = int(deps.cfg("context").get("neighbor_children", 1))
    kids = deps.retriever.children_of(parent["parent_id"], parent["lang"])
    if not kids:
        return parent["content_md"][: 4 * int(deps.cfg("context").get("max_parent_tokens", 6000))]
    seqs = hit_seqs or {child_seq(kids[0]["child_id"])}
    keep = {s + d for s in seqs for d in range(-n, n + 1)}
    header = " ".join(x for x in (parent.get("number"), parent.get("title")) if x)
    out, last = [header] if header else [], None
    for k in kids:
        s = child_seq(k["child_id"])
        if s in keep:
            if last is not None and s != last + 1:
                out.append("…")
            out.append(k.get("raw_text") or k.get("text") or "")
            last = s
    return "\n".join(out)


def _content(deps: Deps, parent: dict[str, Any], hit_seqs: set[int]) -> str:
    max_tok = int(deps.cfg("context").get("max_parent_tokens", 6000))
    tokens = parent.get("token_count") or deps.count_tokens(parent["content_md"])
    return _trim_parent(deps, parent, hit_seqs) if tokens > max_tok else parent["content_md"]


def _make_block(deps: Deps, profiles: dict[str, Any], parents: list[dict[str, Any]], *, score: float,
                hit_seqs: dict[str, set[int]], is_reference: bool = False, referenced_by: str | None = None
                ) -> ContextBlock:
    first = parents[0]
    pid, meta = _doc_meta(profiles, first["doc_id"])
    prof = profiles.get(pid, {})
    blk = ContextBlock(
        project_id=pid, stream=meta.get("stream"), priority=stream_priority(prof, meta.get("stream")),
        doc_id=first["doc_id"], doc_title=meta.get("title"), revision=meta.get("revision"),
        section_key=first["section_key"], number=first.get("number"),
        title=next((p.get("title") for p in parents if p["lang"] == "vi" and p.get("title")), first.get("title")),
        page_start=min((p["page_start"] for p in parents if p.get("page_start") is not None), default=None),
        page_end=max((p["page_end"] for p in parents if p.get("page_end") is not None), default=None),
        is_reference=is_reference, referenced_by=referenced_by, score=score,
    )
    for p in parents:
        setattr(blk, p["lang"], _content(deps, p, hit_seqs.get(p["parent_id"], set())))
    blk.tokens = sum(deps.count_tokens(t) for t in (blk.vi, blk.en) if t)
    return blk


def _group(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["lang"] != "vi"):  # vi trước en
        out.setdefault((r["doc_id"], r["section_key"]), []).append(r)
    return out


def build_context(deps: Deps, reranked: list[dict[str, Any]], profiles: dict[str, Any],
                  extra_parent_ids: list[str] | None = None) -> list[dict[str, Any]]:
    cfg = deps.cfg("context")
    hops = int(cfg.get("reference_hops", 1))
    # điểm cao nhất theo (doc_id, section_key) + seq child trúng theo parent
    best: dict[tuple[str, str], float] = {}
    hit_seqs: dict[str, set[int]] = {}
    for h in reranked:
        key = (h["doc_id"], h["section_key"])
        best[key] = max(best.get(key, 0.0), float(h.get("rerank_score") or 0.0))
        hit_seqs.setdefault(h["parent_id"], set()).add(child_seq(h["child_id"]))
    # child EN trúng → dùng cùng seq cho parent VN (và ngược lại) khi phải cắt parent dài
    for h in reranked:
        other = h["parent_id"].replace(f":{h['lang']}:", ":en:" if h["lang"] == "vi" else ":vi:", 1)
        hit_seqs.setdefault(other, set()).update(hit_seqs[h["parent_id"]])

    groups = _group(deps.repo.parents_by_sections(list(best)))
    primary = [_make_block(deps, profiles, groups[k], score=best[k], hit_seqs=hit_seqs)
               for k in sorted(groups, key=lambda k: -best[k])]
    loaded = set(groups)

    # tham chiếu 1 hop
    refs_of: dict[tuple[str, str], list[str]] = {}
    for k, rows in groups.items():
        refs_of[k] = list(dict.fromkeys(r for p in rows for r in p.get("referenced_ids") or []))
    ref_blocks: dict[tuple[str, str], list[ContextBlock]] = {}
    if hops > 0:
        internal = sorted({r for rs in refs_of.values() for r in rs if is_internal_ref(r)})
        found = _group(deps.repo.find_sections(sorted({d for d, _ in loaded}), internal)) if internal else {}
        standards = sorted({r for rs in refs_of.values() for r in rs if not is_internal_ref(r)})
        std_in_sys = deps.repo.standards_in_system(standards) if standards else {}
        used: set[tuple[str, str]] = set()
        for blk, k in zip(primary, sorted(groups, key=lambda k: -best[k])):
            for r in refs_of[k]:
                if is_internal_ref(r):
                    match = [fk for fk in found if fk[0] == k[0] and section_matches(fk[1], r)]
                    if not match:
                        blk.unresolved_refs.append(r)
                    for fk in match:
                        if fk in loaded or fk in used:
                            continue
                        used.add(fk)
                        ref_blocks.setdefault(k, []).append(_make_block(
                            deps, profiles, found[fk], score=blk.score, hit_seqs={}, is_reference=True,
                            referenced_by=blk.section_key))
                elif r not in std_in_sys:
                    blk.unresolved_refs.append(r)
        loaded |= used

    # Targeted Fetch (ID lấy thẳng)
    extra = []
    if extra_parent_ids:
        rows = [p for p in deps.repo.parents_by_ids(extra_parent_ids) if (p["doc_id"], p["section_key"]) not in loaded]
        for k, ps in _group(rows).items():
            extra.append(_make_block(deps, profiles, ps, score=0.0, hit_seqs={}, is_reference=True,
                                     referenced_by="targeted_fetch"))

    # sắp theo điểm, tham chiếu đi ngay sau khối gốc; cắt theo ngân sách token
    ordered: list[ContextBlock] = []
    for blk, k in zip(primary, sorted(groups, key=lambda k: -best[k])):
        ordered.append(blk)
        ordered += ref_blocks.get(k, [])
    ordered += extra
    ordered = dedup_blocks(ordered, float(cfg.get("dedup_threshold", 0)))
    budget = int(cfg.get("token_budget", 24000))
    out, used_tok = [], 0
    for blk in ordered:
        if used_tok + blk.tokens > budget:
            continue
        used_tok += blk.tokens
        out.append(blk)
    for i, blk in enumerate(out, start=1):
        blk.n = i
    return [b.model_dump() for b in out]
