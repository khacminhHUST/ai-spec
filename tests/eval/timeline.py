"""Timeline vòng lặp agent (không gọi mạng): ghép sự kiện đo được (llm role=agent, tool) thành từng bước LLM → tool,
chuỗi tóm tắt để in, thống kê vòng lặp cho cả bộ (% vượt max_ok_s, phân bố số bước, số lần run_code/read_file).
Sự kiện do tests/eval/run_eval.py ghi: {"kind", "dur", "at" (thời điểm KẾT THÚC, tính từ đầu lượt), ...}.
Unit test: tests/eval/test_eval_timeline.py.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

READ_TOOLS = ("run_code", "read_file")


def _start(e: dict[str, Any]) -> float:
    return round(e["at"] - e["dur"], 3)


def agent_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """→ [{step, start, dur, ttft, n_tool_calls, tool_calls, tool_runs:[{name,start,dur}], in_tok, out_tok}].
    Tool chạy sau khi bước i kết thúc và trước khi bước i+1 bắt đầu được gắn vào bước i."""
    steps = sorted((e for e in events if e.get("kind") == "llm" and e.get("role") == "agent"), key=_start)
    tools = sorted((e for e in events if e.get("kind") == "tool"), key=_start)
    out: list[dict[str, Any]] = []
    for i, s in enumerate(steps):
        lo = s["at"] - 1e-3
        hi = _start(steps[i + 1]) + 1e-3 if i + 1 < len(steps) else float("inf")
        runs = [{"name": t.get("name"), "start": _start(t), "dur": t["dur"]} for t in tools if lo <= _start(t) < hi]
        names = list(s.get("tool_calls") or [])
        out.append({"step": i + 1, "start": _start(s), "dur": s["dur"], "ttft": s.get("ttft"),
                    "n_tool_calls": len(names), "tool_calls": names, "tool_runs": runs,
                    "in_tok": s.get("in_tok"), "out_tok": s.get("out_tok")})
    return out


def timeline_str(tl: list[dict[str, Any]]) -> str:
    """VD: 'A1 0.0+2.3s→search_project_docs | search_project_docs 8.1s | A2 10.5+3.9s→trả lời'."""
    parts: list[str] = []
    for s in tl:
        calls = ",".join(_compact(s["tool_calls"])) or "trả lời"
        parts.append(f"A{s['step']} {s['start']:.1f}+{s['dur']:.1f}s→{calls}")
        if s["tool_runs"]:
            parts.append(", ".join(f"{t['name']} {t['dur']:.1f}s" for t in s["tool_runs"]))
    return " | ".join(parts)


def _compact(names: list[str]) -> list[str]:
    """['a','a','b'] → ['a×2','b'] (giữ thứ tự xuất hiện)."""
    c = Counter(names)
    return [f"{n}×{c[n]}" if c[n] > 1 else n for n in dict.fromkeys(names)]


def tool_counts(tool_names: list[str]) -> dict[str, int]:
    c = Counter(tool_names)
    return {"n_run_code": c.get("run_code", 0), "n_read_file": c.get("read_file", 0)}


def over_limit(r: dict[str, Any]) -> bool | None:
    """Lượt được chấm vượt max_ok_s? None khi câu không khai max_ok_s hoặc lỗi."""
    lim, t = r.get("max_ok_s"), r.get("total_s")
    if lim is None or t is None or r.get("error"):
        return None
    return t > lim


def loop_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """% vượt max_ok_s, phân bố số bước LLM agent, tổng / TB run_code + read_file, số câu có kết quả bị offload."""
    ok = [r for r in results if not r.get("error")]
    flags = [f for f in (over_limit(r) for r in results) if f is not None]
    steps = [r["n_agent_llm"] for r in ok if r.get("n_agent_llm") is not None]
    rc = [r.get("n_run_code", 0) for r in ok]
    rf = [r.get("n_read_file", 0) for r in ok]
    return {
        "n_with_limit": len(flags), "n_over_limit": sum(flags),
        "pct_over_limit": round(100 * sum(flags) / len(flags), 1) if flags else None,
        "over_limit_ids": [r["id"] for r in results if over_limit(r)],
        "agent_steps_dist": dict(sorted(Counter(steps).items())),
        "agent_steps_p50": statistics.median(steps) if steps else None,
        "agent_steps_max": max(steps) if steps else None,
        "n_run_code": sum(rc), "n_read_file": sum(rf),
        "avg_run_code_read_file": round(statistics.mean(a + b for a, b in zip(rc, rf)), 2) if ok else None,
        "n_offloaded": sum(1 for r in ok if r.get("n_offloaded")),
    }
