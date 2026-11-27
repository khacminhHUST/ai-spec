"""Chấm điểm bộ câu hỏi đánh giá (không gọi mạng): chuẩn hóa, must_include/must_not_include, gộp với LLM judge,
thống kê tổng. Dùng bởi tests/eval/run_eval.py; unit test: tests/eval/test_eval_scoring.py.
"""
from __future__ import annotations

import json
import re
import statistics
import unicodedata
from typing import Any

INCLUDE_MIN_WITH_CORRECT = 0.67
_DEC_COMMA = re.compile(r"(?<=\d),(?=\d)")
_DROP = re.compile(r"[\s*`_]+")          # khoảng trắng + ký hiệu markdown (**12 mm** → 12mm)
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "×": "x"})


def normalize(text: str | None) -> str:
    """lowercase, NFKC (² → 2, ký tự full-width), dấu phẩy thập phân → chấm, bỏ khoảng trắng / ký hiệu markdown."""
    t = unicodedata.normalize("NFKC", text or "").lower().translate(_DASHES)
    t = _DEC_COMMA.sub(".", t)
    return _DROP.sub("", t)


def _pattern(needle: str) -> re.Pattern:
    """Biến thể bắt đầu/kết thúc bằng chữ số không khớp giữa số khác (600 ≠ 6000, 12mm ≠ 112mm)."""
    pre = r"(?<![\d.])" if needle[:1].isdigit() else ""
    if needle[-1:].isdigit():
        post = r"(?![\d])"
    elif re.search(r"\dm$", needle):
        post = r"(?!m)"                  # "7m" không khớp "7mm" (văn bản đã bỏ khoảng trắng nên chỉ chặn đúng "mm")
    else:
        post = ""
    return re.compile(pre + re.escape(needle) + post)


def contains(answer_norm: str, variant: str) -> bool:
    v = normalize(variant)
    return bool(v) and bool(_pattern(v).search(answer_norm))


def variants(item: Any) -> list[str]:
    return [str(x) for x in item] if isinstance(item, (list, tuple)) else [str(item)]


def deterministic(answer: str, must_include: list | None, must_not_include: list | None) -> dict[str, Any]:
    """→ include_ratio (1.0 nếu không có must_include), danh sách thiếu, must_not_include vi phạm."""
    norm = normalize(answer)
    inc = must_include or []
    hits, missing = 0, []
    for item in inc:
        if any(contains(norm, v) for v in variants(item)):
            hits += 1
        else:
            missing.append(item)
    violated = [item for item in (must_not_include or []) if any(contains(norm, v) for v in variants(item))]
    return {"include_ratio": round(hits / len(inc), 3) if inc else 1.0, "include_hits": hits,
            "include_total": len(inc), "missing": missing, "must_not_violated": violated}


def combine(det: dict[str, Any], judge: dict[str, Any] | None) -> dict[str, Any]:
    """pass = (include đủ, không vi phạm, verdict != wrong, không bịa) hoặc (correct, include ≥ 0.67, không bịa).
    Không có judge (--no-judge / judge lỗi) → chỉ xét deterministic."""
    ratio, clean = det["include_ratio"], not det["must_not_violated"]
    if judge is None or judge.get("verdict") is None:
        rule_a = ratio == 1.0 and clean
        return {"rule_a": rule_a, "rule_b": None, "pass": rule_a, "judged": False}
    verdict, halluc = judge.get("verdict"), bool(judge.get("hallucination"))
    rule_a = ratio == 1.0 and clean and verdict != "wrong" and not halluc
    rule_b = verdict == "correct" and ratio >= INCLUDE_MIN_WITH_CORRECT and not halluc
    return {"rule_a": rule_a, "rule_b": rule_b, "pass": bool(rule_a or rule_b), "judged": True}


def parse_judge(text: str) -> dict[str, Any]:
    """Text LLM → {verdict, hallucination, reason}; bỏ code fence, lấy object JSON đầu tiên."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    obj = json.loads(m.group(0) if m else t)
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in ("correct", "partial", "wrong"):
        raise ValueError(f"verdict không hợp lệ: {verdict!r}")
    h = obj.get("hallucination")
    if isinstance(h, str):
        h = h.strip().lower() in ("true", "yes", "1")
    return {"verdict": verdict, "hallucination": bool(h), "reason": str(obj.get("reason", ""))}


# ------------------------------------------------------------------ thống kê

def pct(values: list[float], q: float) -> float | None:
    """Phân vị nội suy tuyến tính (q trong 0..1)."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def summarize(results: list[dict[str, Any]], wall_s: float | None = None) -> dict[str, Any]:
    """Tổng: accuracy (% pass, câu lỗi tính là fail), theo type, p50/p90/max total_s, p50 ttft_s, TB số search."""
    ok = [r for r in results if not r.get("error")]
    by_type: dict[str, list[bool]] = {}
    for r in results:
        by_type.setdefault(r.get("type") or "?", []).append(bool(r.get("pass")))
    totals = [r["total_s"] for r in ok if r.get("total_s") is not None]
    ttfts = [r["ttft_s"] for r in ok if r.get("ttft_s")]
    n_pass = sum(1 for r in results if r.get("pass"))
    return {
        "n": len(results), "n_pass": n_pass, "n_error": len(results) - len(ok),
        "accuracy": round(100 * n_pass / len(results), 1) if results else None,
        "accuracy_by_type": {t: {"n": len(v), "pass": sum(v), "accuracy": round(100 * sum(v) / len(v), 1)}
                             for t, v in sorted(by_type.items())},
        "total_s_p50": pct(totals, 0.5), "total_s_p90": pct(totals, 0.9),
        "total_s_max": round(max(totals), 2) if totals else None,
        "ttft_s_p50": pct(ttfts, 0.5),
        "avg_n_search": round(statistics.mean(r.get("n_search", 0) for r in ok), 2) if ok else None,
        "avg_include_ratio": round(statistics.mean(r.get("include_ratio", 0) for r in ok), 3) if ok else None,
        "wall_s": round(wall_s, 1) if wall_s is not None else None,
    }
