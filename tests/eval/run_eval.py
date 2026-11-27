"""Đánh giá agent thật (như app, không qua HTTP): chạy bộ câu hỏi, đo tốc độ, chấm độ chính xác, xuất báo cáo.

python -m tests.eval.run_eval --suite tests/fixtures/ct05_golden.yaml [--ns prod] [--ids c01,c05] [--repeat 1]
    [--concurrency 1] [--out artifacts/eval/<tên>.json] [--no-judge] [--compare old.json]

Dựng: Deps.default → build_runtime → build_assistant_graph(PostgresSaver) → stream_assistant_turn (mỗi câu 1
conversation mới, user_id "eval_user"). Thời gian từng bước đo bằng monkeypatch (ghi theo contextvar nên chạy song
song vẫn tách được theo câu). Chấm: tests/eval/scoring.py (must_include/must_not_include) + LLM judge.
Câu có `turns`: chạy các lượt trước trong cùng conversation, chỉ chấm lượt cuối. Mỗi lượt ghi timeline bước agent
(mỗi lần gọi LLM agent: bắt đầu, thời lượng, tool gọi; mỗi tool: thời lượng) – tests/eval/timeline.py.
"""
from __future__ import annotations

import argparse
import contextvars
import json
import os
import sys
import threading
import time
import traceback
import uuid
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from tests.eval import scoring
from tests.eval import timeline as TL

USER_ID = "eval_user"
JUDGE_MODEL = "gemini-3.8-flash"

REC: contextvars.ContextVar[list | None] = contextvars.ContextVar("eval_rec", default=None)
T0: contextvars.ContextVar[float] = contextvars.ContextVar("eval_t0", default=0.0)
TAG: contextvars.ContextVar[str | None] = contextvars.ContextVar("eval_tag", default=None)
IN_SEARCH: contextvars.ContextVar[bool] = contextvars.ContextVar("eval_in_search", default=False)
PRINT_LOCK = threading.Lock()


# ------------------------------------------------------------------ đo thời gian (monkeypatch)

def rec(kind: str, dur: float, **kw: Any) -> None:
    events = REC.get()
    if events is not None:
        events.append({"kind": kind, "dur": round(dur, 3), "at": round(time.perf_counter() - T0.get(), 3), **kw})


def _timed(mod: Any, name: str, kind: str, extra=None, tag: str | None = None, search: bool = False) -> None:
    orig = getattr(mod, name)

    def wrap(*a, **k):
        tokens = [TAG.set(tag)] if tag else []
        s_tok = IN_SEARCH.set(True) if search else None
        t = time.perf_counter()
        info: dict[str, Any] = {}
        try:
            out = orig(*a, **k)
            if extra:
                try:
                    info = extra(a, k, out)
                except Exception:  # noqa: BLE001
                    info = {}
            return out
        finally:
            rec(kind, time.perf_counter() - t, **info)
            for tk in tokens:
                TAG.reset(tk)
            if s_tok is not None:
                IN_SEARCH.reset(s_tok)
    setattr(mod, name, wrap)


def _search_info(a, k, out) -> dict[str, Any]:
    q = a[5] if len(a) > 5 else k.get("question")
    try:
        status = json.loads(out[0]).get("status")
    except Exception:  # noqa: BLE001
        status = None
    return {"q": q, "status": status, "n_cites": len(out[1] or [])}


def patch_all() -> None:
    """Gọi SAU khi đặt MEP_NS, TRƯỚC khi dựng graph."""
    import concurrent.futures.thread as cft

    # ThreadPoolExecutor của app (SearchDocs, retrieval, reranker) không chép contextvars → chép để ghi đúng câu.
    orig_submit = cft.ThreadPoolExecutor.submit

    def submit(self, fn, /, *a, **k):
        return orig_submit(self, contextvars.copy_context().run, fn, *a, **k)
    cft.ThreadPoolExecutor.submit = submit

    import app.providers.reranker as RR
    import app.repositories.qdrant as RQ
    import app.services.agent.tools.search_docs as SD
    import app.services.query.grader as GR
    import app.services.query.graph as G
    from langchain_google_genai import ChatGoogleGenerativeAI as C

    cands = lambda a, k, o: {"candidates": len({c["child_id"] for c in a[2]})}  # noqa: E731
    _timed(G, "run_router", "n1_router", tag="router")
    _timed(G, "resolve", "n2_resolve")
    _timed(G, "run_branch", "n3_branch", extra=lambda a, k, o: {"branch": f"{a[1]['stream']}/{a[1]['lang']}"})
    _timed(G, "rerank", "n4_rerank", extra=cands)
    _timed(GR, "rerank", "n6_fetch_rerank", extra=cands)
    _timed(G, "build_context", "n5_context",
           extra=lambda a, k, o: {"blocks": len(o), "tokens": sum(b.get("tokens") or 0 for b in o)})
    _timed(G, "grade", "n6_grade")
    _timed(GR, "llm_check", "n6_grader_llm", tag="grader")
    _timed(G, "targeted_fetch", "n6_targeted_fetch")
    _timed(SD, "expand_sibling_tables", "sibling_tables")
    _timed(SD, "fit_or_offload", "fit_or_offload", extra=lambda a, k, o: {"offloaded": o[1] is not None})
    _timed(SD, "search_impl", "search_total", extra=_search_info, search=True)
    _timed(RQ.QdrantRetriever, "_embed", "embed_query")
    _timed(RR.GeminiReranker, "_score_batch", "rerank_batch", extra=lambda a, k, o: {"n": len(a[2])}, tag="rerank")

    def role() -> str:
        return TAG.get() or ("search_other" if IN_SEARCH.get() else "agent")

    def usage(msg) -> dict[str, Any]:
        u = getattr(msg, "usage_metadata", None) or {}
        return {"in_tok": u.get("input_tokens"), "out_tok": u.get("output_tokens")}

    og, os_ = C._generate, C._stream

    def gen(self, *a, **k):
        t = time.perf_counter()
        r = og(self, *a, **k)
        msg = r.generations[0].message
        names = [c["name"] for c in getattr(msg, "tool_calls", None) or []]
        rec("llm", time.perf_counter() - t, role=role(), model=self.model, tool_calls=names, **usage(msg))
        return r

    import app.services.agent.loop.hedge as HM
    HC = HM.HedgedChatGoogleGenerativeAI

    def timed_stream(orig, skip=None):
        def stream(self, *a, **k):
            if skip is not None and isinstance(self, skip):     # model agent (hedge) đã đo ở lớp con
                yield from orig(self, *a, **k)
                return
            t = time.perf_counter()
            first, last, names = None, None, []
            for ch in orig(self, *a, **k):
                if first is None:
                    first = time.perf_counter() - t
                if getattr(ch.message, "usage_metadata", None):
                    last = ch.message
                names += [c["name"] for c in getattr(ch.message, "tool_call_chunks", None) or [] if c.get("name")]
                yield ch
            rec("llm", time.perf_counter() - t, role=role(), model=self.model, ttft=round(first or 0, 3),
                tool_calls=names, **(usage(last) if last else {}))
        return stream
    C._generate, C._stream = gen, timed_stream(os_, skip=HC)
    # Model agent có hedge (loop/hedge.py): đo ở _stream của lớp con (request phụ chạy trong thread, không ghi).
    HC._stream = timed_stream(HC._stream)
    onh = HM.note_hedge

    def note_hedge(attempt, waited_s):
        rec("hedge", 0.0, attempt=attempt, waited_s=round(waited_s, 2))
        onh(attempt, waited_s)
    HM.note_hedge = note_hedge
    onw = HM.note_hedge_winner

    def note_hedge_winner(winner, waited_s):
        rec("hedge_win", 0.0, winner=winner, waited_s=round(waited_s, 2))
        onw(winner, waited_s)
    HM.note_hedge_winner = note_hedge_winner

    # Thời lượng từng lần chạy tool của agent (ToolNode → BaseTool.invoke → run / ainvoke → arun).
    from langchain_core.tools import BaseTool
    orun, oarun = BaseTool.run, BaseTool.arun

    def run(self, *a, **k):
        t = time.perf_counter()
        try:
            return orun(self, *a, **k)
        finally:
            rec("tool", time.perf_counter() - t, name=self.name)

    async def arun(self, *a, **k):
        t = time.perf_counter()
        try:
            return await oarun(self, *a, **k)
        finally:
            rec("tool", time.perf_counter() - t, name=self.name)
    BaseTool.run, BaseTool.arun = run, arun

    from psycopg import Cursor
    oe = Cursor.execute

    def ex(self, *a, **k):
        t = time.perf_counter()
        try:
            return oe(self, *a, **k)
        finally:
            rec("sql", time.perf_counter() - t)
    Cursor.execute = ex


def timing_summary(events: list[dict]) -> tuple[dict[str, Any], dict[str, Any]]:
    """→ (theo từng loại sự kiện, theo nhóm: search_total, rerank, router, agent_llm, embed…)."""
    agg: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for e in events:
        k = e["kind"] if e["kind"] != "llm" else f"llm:{e['role']}"
        agg[k][0] += 1
        agg[k][1] += e["dur"]
    by_kind = {k: {"n": v[0], "sum_s": round(v[1], 2)} for k, v in sorted(agg.items(), key=lambda x: -x[1][1])}
    s = lambda *ks: round(sum(agg[k][1] for k in ks if k in agg), 2)  # noqa: E731
    groups = {"search_total": s("search_total"), "rerank": s("n4_rerank", "n6_fetch_rerank"),
              "router": s("n1_router"), "agent_llm": s("llm:agent"), "grader": s("n6_grader_llm"),
              "embed": s("embed_query"), "sql": s("sql")}
    return by_kind, groups


# ------------------------------------------------------------------ LLM judge

JUDGE_PROMPT = """Bạn là giám khảo chấm câu trả lời của chatbot tra cứu tài liệu dự án cơ điện (MEP).

Quy tắc:
- correct: câu trả lời có đủ các ý chính của đáp án tham chiếu và không mâu thuẫn số liệu (giá trị, đơn vị, điều kiện).
  Thông tin bổ sung đúng/hợp lý khác không bị trừ điểm. Cách diễn đạt, ngôn ngữ (Việt/Anh), thứ tự không quan trọng.
- partial: đúng một phần ý chính, thiếu ý quan trọng, nhưng không có số liệu sai.
- wrong: sai hoặc mâu thuẫn số liệu chính với đáp án tham chiếu, trả lời lạc đề, hoặc nói "không có trong tài liệu"
  trong khi đáp án tham chiếu có thông tin.
- Câu loại not_found (trạng thái mong đợi NOT_FOUND): correct khi câu trả lời nói rõ tài liệu dự án không có thông tin
  này và KHÔNG tự đưa ra số liệu cụ thể; nếu tự điền số liệu như thể từ tài liệu → wrong và hallucination=true.
- hallucination=true khi câu trả lời khẳng định số liệu/yêu cầu cụ thể mà mâu thuẫn với đáp án tham chiếu hoặc rõ ràng
  bịa ra (không thể có trong tài liệu), không chỉ vì có thêm chi tiết đúng mà đáp án tham chiếu không nhắc.{extra_rules}

Loại câu: {type}
Trạng thái mong đợi: {expected_status}
{context}Câu hỏi: {question}

Đáp án tham chiếu:
{reference}

Câu trả lời cần chấm:
{answer}

Chỉ trả về đúng 1 object JSON, không giải thích ngoài JSON:
{{"verdict": "correct|partial|wrong", "hallucination": true|false, "reason": "<1-2 câu tiếng Việt>"}}"""


PARTIAL_RULE = ("\n- Câu loại partial (tài liệu chỉ có một phần): correct khi trả lời đúng phần có trong đáp án tham chiếu VÀ"
                "\n  nói rõ phần nào tài liệu dự án không có; tự điền số liệu cho phần không có → wrong và hallucination=true.")
TURNS_RULE = "\n- Câu hỏi là lượt tiếp nối: hiểu câu hỏi theo các lượt hỏi trước trong hội thoại."


def judge_prompt(q: dict[str, Any], answer: str) -> str:
    """Câu thường (bộ golden) → prompt y như cũ; partial / nhiều lượt thêm quy tắc + ngữ cảnh."""
    turns = list(q.get("turns") or [])
    extra = (PARTIAL_RULE if q.get("type") == "partial" else "") + (TURNS_RULE if turns else "")
    context = ("Các lượt hỏi trước trong hội thoại:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(turns, 1)) + "\n"
               if turns else "")
    return JUDGE_PROMPT.format(type=q.get("type") or "", expected_status=q.get("expected_status") or "OK",
                               question=q["question"], reference=q.get("reference_answer") or "(không có)",
                               answer=answer or "(rỗng)", extra_rules=extra, context=context)


class Judge:
    def __init__(self, settings) -> None:
        from app.providers.llm import get_chat_model

        self.model = get_chat_model("main", settings, model=JUDGE_MODEL, temperature=0, thinking_level="low")

    def __call__(self, q: dict[str, Any], answer: str) -> dict[str, Any]:
        from app.providers.llm import message_text

        prompt = judge_prompt(q, answer)
        err: Exception | None = None
        for _ in range(3):
            try:
                t = time.perf_counter()
                out = scoring.parse_judge(message_text(self.model.invoke(prompt)))
                return {**out, "judge_s": round(time.perf_counter() - t, 2)}
            except Exception as e:  # noqa: BLE001
                err = e
        return {"verdict": None, "hallucination": None, "reason": None, "error": f"judge lỗi: {err}"}


# ------------------------------------------------------------------ chạy 1 câu

def run_turn(graph, question: str, cid: str) -> dict[str, Any]:
    """1 lượt hỏi trong conversation `cid` → số đo của lượt (thời gian, tool, timeline bước agent, câu trả lời).
    Exception lan ra ngoài kèm thuộc tính `partial` (số đo tới lúc lỗi)."""
    from app.services.query.graph import turn_config

    events: list[dict] = []
    rec_tok, t0_tok = REC.set(events), T0.set(time.perf_counter())
    t0 = T0.get()
    ttft, streamed, tools, citations, status = None, "", [], [], None
    try:
        for ev in graph_stream(graph, question, cid):
            kind, data = ev["event"], ev.get("data")
            if kind == "token":
                if ttft is None:
                    ttft = time.perf_counter() - t0
                streamed += data
            elif kind == "token_reset":
                streamed, ttft = "", None
            elif kind == "tool_start":
                tools.append({"tool": data["tool"], "args": data.get("args_preview", ""),
                              "at": round(time.perf_counter() - t0, 2)})
            elif kind == "citations":
                citations = data or []
            elif kind == "done":
                status = (data or {}).get("status")
        total = time.perf_counter() - t0
        st = graph.get_state(turn_config(cid)).values
        answer = st.get("answer") or streamed
    except Exception as e:  # noqa: BLE001
        e.partial = {"total_s": round(time.perf_counter() - t0, 2), "answer": streamed}  # type: ignore[attr-defined]
        raise
    finally:
        REC.reset(rec_tok)
        T0.reset(t0_tok)
    by_kind, groups = timing_summary(events)
    searches = [e for e in events if e["kind"] == "search_total"]
    tl = TL.agent_timeline(events)
    return {
        "total_s": round(total, 2), "ttft_s": round(ttft, 2) if ttft is not None else None, "status": status,
        "n_tools": len(tools), "tools": tools,
        "n_search": sum(1 for t in tools if t["tool"] == "search_project_docs"),
        **TL.tool_counts([t["tool"] for t in tools]),
        "n_offloaded": sum(1 for e in events if e["kind"] == "fit_or_offload" and e.get("offloaded")),
        "search_statuses": [e.get("status") for e in searches],
        "n_agent_llm": len(tl), "timeline": tl, "timeline_str": TL.timeline_str(tl),
        "timing": groups, "timing_by_kind": by_kind,
        "answer": answer, "citations": citations,
        "events": [e for e in events if e["kind"] != "sql"],
    }


TURN_KEYS = ("total_s", "ttft_s", "status", "n_tools", "n_search", "n_run_code", "n_read_file", "n_agent_llm",
             "timeline_str")


def run_question(graph, manager, q: dict[str, Any], run: int, judge: Judge | None) -> dict[str, Any]:
    """Các lượt `turns` (nếu có) chạy trước trong cùng conversation, chỉ chấm lượt cuối (`question`).
    Số đo ở mức câu (total_s, n_search, timeline…) là của lượt cuối; turns[] ghi tóm tắt từng lượt trước."""
    cid = f"eval-{uuid.uuid4().hex}"
    prior = list(q.get("turns") or [])
    r: dict[str, Any] = {"id": q["id"], "run": run, "type": q.get("type"), "question": q["question"],
                         "expected_status": q.get("expected_status") or "OK", "conversation_id": cid,
                         "max_ok_s": q.get("max_ok_s")}
    turns: list[dict[str, Any]] = []
    try:
        for i, text in enumerate(prior, 1):
            m = run_turn(graph, text, cid)
            turns.append({"turn": i, "question": text, **{k: m.get(k) for k in TURN_KEYS},
                          "answer": (m.get("answer") or "")[:600]})
        r.update(run_turn(graph, q["question"], cid))
    except Exception as e:  # noqa: BLE001 – 1 câu lỗi không dừng cả bộ
        part = getattr(e, "partial", {})
        r.update({"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-3000:],
                  "total_s": part.get("total_s"), "answer": part.get("answer", ""), "pass": False})
    finally:
        try:
            manager.destroy(cid)
        except Exception:  # noqa: BLE001
            pass
    if prior:
        r["turns"] = turns
        r["turns_total_s"] = round(sum(t["total_s"] or 0 for t in turns) + (r.get("total_s") or 0), 2)
    if r.get("error"):
        return r
    det = scoring.deterministic(r["answer"], q.get("must_include"), q.get("must_not_include"))
    jd = judge(q, r["answer"]) if judge else None
    r.update({**det, "judge": jd, "verdict": (jd or {}).get("verdict"), "hallucination": (jd or {}).get("hallucination"),
              "score": scoring.combine(det, jd)})
    r["pass"] = r["score"]["pass"]
    return r


def graph_stream(graph, question: str, cid: str):
    from app.services.agent.graph import stream_assistant_turn

    yield from stream_assistant_turn(graph, USER_ID, question, cid)


# ------------------------------------------------------------------ báo cáo

def _f(v: Any, w: int, nd: int = 1) -> str:
    if v is None:
        s = "-"
    elif isinstance(v, bool):
        s = "Y" if v else "n"
    elif isinstance(v, float):
        s = f"{v:.{nd}f}"
    else:
        s = str(v)
    return s.ljust(w) if not isinstance(v, (int, float)) or isinstance(v, bool) else s.rjust(w)


def print_report(results: list[dict], summary: dict) -> None:
    print("\n" + "id".ljust(10) + "run " + "type".ljust(13) + "pass " + "incl".rjust(5) + "  " + "verdict".ljust(8)
          + "hal " + "total_s".rjust(8) + "ttft_s".rjust(8) + "ok_s".rjust(5) + "search".rjust(7) + "steps".rjust(6)
          + "rc/rf".rjust(6) + "  error")
    for r in results:
        over = TL.over_limit(r)
        rcrf = f"{r.get('n_run_code', 0)}/{r.get('n_read_file', 0)}" if not r.get("error") else None
        print(_f(r["id"], 10) + _f(r["run"], 3) + " " + _f(r.get("type"), 13) + _f(r.get("pass"), 5)
              + _f(r.get("include_ratio"), 5, 2) + "  " + _f(r.get("verdict"), 8) + _f(r.get("hallucination"), 4)
              + _f(r.get("total_s"), 8) + _f(r.get("ttft_s"), 8)
              + (f"{'VƯỢT' if over else 'ok'}".rjust(5) if over is not None else "-".rjust(5))
              + _f(r.get("n_search"), 7) + _f(r.get("n_agent_llm"), 6) + (rcrf or "-").rjust(6)
              + "  " + (r.get("error") or "")[:60])
    print("\nTimeline (A<bước> <bắt đầu>+<thời lượng>s→tool gọi | thời lượng từng tool):")
    for r in results:
        for t in r.get("turns") or []:
            print(f"  {r['id']}#{r['run']} lượt {t['turn']} ({t['total_s']}s): {t.get('timeline_str') or '-'}")
        if r.get("timeline_str") is not None:
            tag = " lượt cuối" if r.get("turns") else ""
            print(f"  {r['id']}#{r['run']}{tag} ({r.get('total_s')}s): {r['timeline_str'] or '-'}")
    s = summary
    print(f"\nAccuracy: {s['accuracy']}% ({s['n_pass']}/{s['n']}, lỗi {s['n_error']})"
          f" · include TB {s['avg_include_ratio']}")
    for t, v in s["accuracy_by_type"].items():
        print(f"  {t:13s} {v['accuracy']:5.1f}% ({v['pass']}/{v['n']})")
    print(f"total_s p50 {s['total_s_p50']} · p90 {s['total_s_p90']} · max {s['total_s_max']}"
          f" · ttft_s p50 {s['ttft_s_p50']} · search TB {s['avg_n_search']} · thời gian chạy {s['wall_s']}s")
    if s.get("n_with_limit"):
        print(f"Vượt max_ok_s: {s['pct_over_limit']}% ({s['n_over_limit']}/{s['n_with_limit']})"
              + (f" – {', '.join(s['over_limit_ids'])}" if s["over_limit_ids"] else ""))
    print(f"Bước LLM agent: phân bố {s.get('agent_steps_dist')} · p50 {s.get('agent_steps_p50')} · max {s.get('agent_steps_max')}"
          f" · run_code {s.get('n_run_code')} · read_file {s.get('n_read_file')} (TB {s.get('avg_run_code_read_file')}/câu)"
          f" · câu có kết quả offload {s.get('n_offloaded')}")


def _by_id(results: list[dict]) -> dict[str, dict[str, Any]]:
    """Gộp các lần lặp theo id: tỉ lệ pass, TB include/total_s/n_search."""
    g: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        g[r["id"]].append(r)
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    return {i: {"pass": mean([1.0 if r.get("pass") else 0.0 for r in rs]),
                "include_ratio": mean([r["include_ratio"] for r in rs if r.get("include_ratio") is not None]),
                "total_s": mean([r["total_s"] for r in rs if not r.get("error") and r.get("total_s") is not None]),
                "n_search": mean([r["n_search"] for r in rs if r.get("n_search") is not None])}
            for i, rs in g.items()}


def _d(old: Any, new: Any) -> str:
    if old is None or new is None:
        return f"{old} → {new}"
    return f"{old} → {new} ({new - old:+.2f})"


def print_compare(old: dict, new_results: list[dict], new_summary: dict) -> None:
    o, n = _by_id(old.get("results") or []), _by_id(new_results)
    print(f"\n=== So sánh với {old.get('meta', {}).get('out') or 'bản cũ'}")
    print("id".ljust(10) + "pass".ljust(22) + "include".ljust(24) + "total_s".ljust(28) + "search")
    for i in sorted(set(o) | set(n)):
        a, b = o.get(i, {}), n.get(i, {})
        mark = "" if not a or not b or a.get("pass") == b.get("pass") else ("  ↑" if (b.get("pass") or 0) > (a.get("pass") or 0) else "  ↓")
        print(i.ljust(10) + _d(a.get("pass"), b.get("pass")).ljust(22) + _d(a.get("include_ratio"),
              b.get("include_ratio")).ljust(24) + _d(a.get("total_s"), b.get("total_s")).ljust(28)
              + _d(a.get("n_search"), b.get("n_search")) + mark)
    os_ = old.get("summary") or {}
    for k in ("accuracy", "total_s_p50", "total_s_p90", "total_s_max", "ttft_s_p50", "avg_n_search", "pct_over_limit",
              "agent_steps_p50", "avg_run_code_read_file", "wall_s"):
        print(f"  {k:12s} {_d(os_.get(k), new_summary.get(k))}")


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--ns", help="mặc định lấy từ suite")
    ap.add_argument("--ids", default="", help="VD c01,c05")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--out")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--compare", help="file JSON kết quả cũ")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    warnings.filterwarnings("ignore", category=UserWarning)   # cảnh báo pydantic/genai lặp mỗi câu

    suite = yaml.safe_load(Path(a.suite).read_text(encoding="utf-8"))
    ns = a.ns or suite.get("ns")
    if not ns:
        ap.error("thiếu --ns (suite không khai ns)")
    os.environ["MEP_NS"] = ns                    # trước khi import app
    ids = [i.strip() for i in a.ids.split(",") if i.strip()]
    qs = [q for q in suite["questions"] if q.get("question") and (not ids or q["id"] in ids)]
    if ids and (miss := set(ids) - {q["id"] for q in qs}):
        ap.error(f"không có id: {sorted(miss)}")
    out = Path(a.out or f"artifacts/eval/{Path(a.suite).stem}_{datetime.now():%Y%m%d_%H%M%S}.json")

    patch_all()
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.core.config import get_settings
    from app.db.client import checkpointer_conn
    from app.services.agent.graph import build_assistant_graph
    from app.services.agent.runtime import build_runtime
    from app.services.query.deps import Deps

    s = get_settings()
    assert s.ns == ns, f"MEP_NS={s.ns} ≠ {ns}"
    deps = Deps.default(s)
    rt = build_runtime(deps)
    conn = checkpointer_conn(s)
    graph = build_assistant_graph(deps, rt, PostgresSaver(conn))
    judge = None if a.no_judge else Judge(s)
    print(f"Suite {a.suite} · project {suite.get('project')} · ns {ns} · {len(qs)} câu × {a.repeat}"
          f" · concurrency {a.concurrency} · judge {'tắt' if judge is None else JUDGE_MODEL}")

    tasks = [(q, run) for run in range(1, a.repeat + 1) for q in qs]
    results: list[dict] = []
    started, w0 = datetime.now().isoformat(timespec="seconds"), time.perf_counter()

    def one(q, run):
        r = run_question(graph, rt.manager, q, run, judge)
        with PRINT_LOCK:
            print(f"[{len(results) + 1}/{len(tasks)}] {r['id']}#{r['run']} pass={r.get('pass')} "
                  f"incl={r.get('include_ratio')} verdict={r.get('verdict')} total={r.get('total_s')}s "
                  f"ttft={r.get('ttft_s')}s search={r.get('n_search')} steps={r.get('n_agent_llm')}"
                  + (f" turns={[t['total_s'] for t in r['turns']]}" if r.get("turns") else "")
                  + (f" ERROR {r['error']}" if r.get("error") else ""), flush=True)
            results.append(r)
        return r

    try:
        if a.concurrency <= 1:
            for q, run in tasks:
                one(q, run)
        else:
            with ThreadPoolExecutor(max_workers=a.concurrency, thread_name_prefix="eval") as ex:
                for f in as_completed([ex.submit(one, q, run) for q, run in tasks]):
                    f.result()
    finally:
        wall = time.perf_counter() - w0
        order = {(q["id"], run): i for i, (q, run) in enumerate(tasks)}
        results.sort(key=lambda r: order.get((r["id"], r["run"]), 0))
        summary = {**scoring.summarize(results, wall), **TL.loop_summary(results)}
        report = {"meta": {"suite": a.suite, "project": suite.get("project"), "ns": ns, "started": started,
                           "repeat": a.repeat, "concurrency": a.concurrency, "judge": None if judge is None else JUDGE_MODEL,
                           "agent_model": rt.cfg["agent"]["model"], "out": str(out)},
                  "summary": summary, "results": results}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        conn.close()
    print_report(results, summary)
    if a.compare:
        print_compare(json.loads(Path(a.compare).read_text(encoding="utf-8")), results, summary)
    print(f"\nBáo cáo: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
