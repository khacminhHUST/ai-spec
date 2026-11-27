"""Unit: timeline bước agent + thống kê vòng lặp (tests/eval/timeline.py), prompt judge cho câu nhiều lượt/partial."""
from __future__ import annotations

import pytest

from tests.eval import timeline as TL

pytestmark = pytest.mark.unit


def _llm(start, dur, calls=(), role="agent"):
    return {"kind": "llm", "role": role, "dur": dur, "at": start + dur, "tool_calls": list(calls)}


def _tool(name, start, dur):
    return {"kind": "tool", "name": name, "dur": dur, "at": start + dur}


EVENTS = [
    _llm(0.0, 2.0, ["search_project_docs", "search_project_docs"]),
    _llm(2.5, 1.0, role="router"),                       # LLM trong tool → không phải bước agent
    _tool("search_project_docs", 2.1, 8.0),
    _tool("search_project_docs", 2.2, 9.0),
    _llm(11.5, 1.5, ["run_code"]),
    _tool("run_code", 13.1, 0.8),
    _llm(14.0, 3.0),
    {"kind": "sql", "dur": 0.01, "at": 1.0},
]


def test_agent_timeline_groups_tools_under_step():
    tl = TL.agent_timeline(EVENTS)
    assert [s["step"] for s in tl] == [1, 2, 3]
    assert tl[0]["n_tool_calls"] == 2 and [t["name"] for t in tl[0]["tool_runs"]] == ["search_project_docs"] * 2
    assert tl[1]["tool_calls"] == ["run_code"] and tl[1]["tool_runs"][0]["dur"] == 0.8
    assert tl[2]["n_tool_calls"] == 0 and tl[2]["tool_runs"] == [] and tl[2]["start"] == 14.0


def test_timeline_str():
    s = TL.timeline_str(TL.agent_timeline(EVENTS))
    assert s.startswith("A1 0.0+2.0s→search_project_docs×2 | search_project_docs 8.0s, search_project_docs 9.0s")
    assert "A2 11.5+1.5s→run_code | run_code 0.8s" in s and s.endswith("A3 14.0+3.0s→trả lời")
    assert TL.timeline_str([]) == ""


def test_tool_counts_and_over_limit():
    assert TL.tool_counts(["run_code", "read_file", "run_code", "search_project_docs"]) == \
        {"n_run_code": 2, "n_read_file": 1}
    assert TL.over_limit({"max_ok_s": 20, "total_s": 25.0}) is True
    assert TL.over_limit({"max_ok_s": 20, "total_s": 19.0}) is False
    assert TL.over_limit({"total_s": 50}) is None
    assert TL.over_limit({"max_ok_s": 20, "total_s": 50, "error": "x"}) is None


def test_loop_summary():
    rs = [{"id": "a", "max_ok_s": 20, "total_s": 30, "n_agent_llm": 4, "n_run_code": 2, "n_read_file": 1,
           "n_offloaded": 1},
          {"id": "b", "max_ok_s": 30, "total_s": 12, "n_agent_llm": 2, "n_run_code": 0, "n_read_file": 0},
          {"id": "c", "total_s": 9, "n_agent_llm": 2},
          {"id": "d", "max_ok_s": 20, "error": "boom", "total_s": 1}]
    s = TL.loop_summary(rs)
    assert s["n_with_limit"] == 2 and s["n_over_limit"] == 1 and s["pct_over_limit"] == 50.0
    assert s["over_limit_ids"] == ["a"]
    assert s["agent_steps_dist"] == {2: 2, 4: 1} and s["agent_steps_p50"] == 2 and s["agent_steps_max"] == 4
    assert s["n_run_code"] == 2 and s["n_read_file"] == 1 and s["avg_run_code_read_file"] == 1.0
    assert s["n_offloaded"] == 1


def test_judge_prompt_turns_and_partial():
    from tests.eval.run_eval import JUDGE_PROMPT, judge_prompt

    plain = {"id": "c01", "type": "lookup", "question": "Q?", "reference_answer": "R", "expected_status": "OK"}
    old = JUDGE_PROMPT.replace("{extra_rules}", "").replace("{context}", "").format(
        type="lookup", expected_status="OK", question="Q?", reference="R", answer="A")
    assert judge_prompt(plain, "A") == old                 # bộ golden: prompt không đổi
    p = judge_prompt({**plain, "type": "multi_turn", "turns": ["Câu 1", "Câu 2"], "question": "còn X?"}, "A")
    assert "1. Câu 1\n2. Câu 2\nCâu hỏi: còn X?" in p and "lượt tiếp nối" in p
    assert "partial (tài liệu chỉ có một phần)" in judge_prompt({**plain, "type": "partial"}, "A")
