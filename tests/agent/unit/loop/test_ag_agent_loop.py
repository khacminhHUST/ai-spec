"""Unit – vòng lặp agent (LLM giả có tool_calls theo kịch bản, MockSandbox): thứ tự tool, dừng, giới hạn,
lỗi tool, xác nhận tool write, cắt kết quả tool, tóm tắt hội thoại."""
import copy
import itertools
import json

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.services.agent.config import agent_cfg
from app.services.agent.loop.agent import agent_input, agent_result, build_agent
from app.services.agent.loop.confirm import pending_confirms, resume_command
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.agent.skills import load_skills
from app.services.agent.state import AgentContext
from app.services.agent.tools.registry import build_toolset
from tests.agent.fakes import CTX, ScriptedModel, tool_call
from tests.query.fakes import S5, make_deps, router_json, scripted_llm

pytestmark = pytest.mark.unit

BEND = router_json(q="Bán kính uốn cong tối thiểu của cáp tiết diện lớn hơn 25mm2",
                   queries_vi=["bán kính uốn cong tối thiểu cáp tiết diện lớn hơn 25mm2"],
                   queries_en=["minimum bend radius cable larger 25mm2"], exact_keywords=["25mm2"])
PUSHED = []


@tool("push_data")
def push_data(payload: str) -> str:
    """Đẩy dữ liệu sang hệ thống ngoài (giả lập)."""
    PUSHED.append(payload)
    return json.dumps({"ok": True, "pushed": payload})


@tool("boom")
def boom() -> str:
    """Tool luôn ném exception."""
    raise RuntimeError("hỏng")


@tool("big")
def big() -> str:
    """Tool trả kết quả rất dài."""
    return "dòng dữ liệu dài " * 3000


push_data.metadata = {"access": "write"}
boom.metadata = {"access": "read"}
big.metadata = {"access": "read"}


def _agent(mock_settings, script, *, cfg_over=None, checkpointer=None, clock=None):
    deps = make_deps(mock_settings, llm=scripted_llm(BEND))
    boxes = {}

    def factory(cid):
        boxes[cid] = MockSandbox()
        return boxes[cid]

    manager = SandboxManager(factory, 1800)
    cfg = copy.deepcopy(agent_cfg())
    cfg["agent"].update(cfg_over or {})
    skills = load_skills(cfg["skills_dir"])
    ts = build_toolset(deps, manager, cfg, skills, [push_data, boom, big])
    model = ScriptedModel(script=script)
    kw = {"clock": clock} if clock else {}
    agent = build_agent(model, ts, skills, cfg, manager, checkpointer, **kw)
    return agent, model, boxes


def _run(agent, question="Yêu cầu", ctx: AgentContext | None = None, thread="t1"):
    ctx = ctx or AgentContext(CTX.user_id, CTX.conversation_id)
    return agent.invoke(agent_input(ctx, question), {"configurable": {"thread_id": thread}}, context=ctx)


def _tools_run(state):
    return [m.name for m in state["messages"] if isinstance(m, ToolMessage)]


def test_scripted_sequence_runs_in_order_and_stops(mock_settings):
    script = [tool_call("search_project_docs", question="Bán kính uốn cáp > 25mm2?"),
              tool_call("load_skill", name="xlsx-export"),
              tool_call("run_code", code="print('ok')"),
              "Bán kính uốn cong tối thiểu: 6 lần đường kính [1]."]
    agent, model, boxes = _agent(mock_settings, script)
    st = _run(agent)
    assert _tools_run(st) == ["search_project_docs", "load_skill", "run_code"]
    assert len(model.calls) == 4                                   # dừng khi hết tool_calls
    res = agent_result(st)
    assert res["answer"].startswith("Bán kính uốn") and res["stop_reason"] is None
    assert [c["n"] for c in res["citations"]] == [1] and res["active_projects"] == [S5]
    skill_msg = json.loads(st["messages"][4].content)
    assert skill_msg["ok"] and "mep_helpers" in skill_msg["content"]
    assert "xlsx-export" in str(model.calls[0][0].content)        # system prompt liệt kê skill


def test_parallel_searches_get_unique_numbers(mock_settings):
    q = "Bán kính uốn cáp > 25mm2?"
    calls = [{"name": "search_project_docs", "args": {"question": q}, "id": f"c{i}"} for i in range(3)]
    agent, _, _ = _agent(mock_settings, [AIMessage("", tool_calls=calls), "Xong [1]."])
    st = _run(agent)
    ns = [s["n"] for s in st["sources"]]
    assert len(ns) > 3 and sorted(ns) == list(range(1, len(ns) + 1))
    assert st["active_projects"] == [S5]


def test_max_steps_stops_with_message(mock_settings):
    agent, model, _ = _agent(mock_settings, [tool_call("list_files")], cfg_over={"max_steps": 3})
    res = agent_result(_run(agent))
    assert len(model.calls) == 3
    assert "giới hạn 3 bước" in res["answer"] and res["stop_reason"]


def test_consecutive_tool_errors_stop(mock_settings):
    agent, model, _ = _agent(mock_settings, [tool_call("read_file", path="/etc/passwd")],
                             cfg_over={"max_consecutive_tool_errors": 3, "max_steps": 20})
    st = _run(agent)
    errors = [m for m in st["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(errors) == 3 and len(model.calls) == 3
    assert "lỗi 3 lần liên tiếp" in agent_result(st)["answer"]


def test_wall_time_stop(mock_settings):
    clock = itertools.count(0, 100)
    agent, model, _ = _agent(mock_settings, [tool_call("list_files")], cfg_over={"max_wall_time_s": 250},
                             clock=lambda: next(clock))
    res = agent_result(_run(agent))
    assert len(model.calls) == 2 and "thời gian tối đa 250" in res["answer"]


def test_tool_exception_becomes_structured_error(mock_settings):
    agent, model, _ = _agent(mock_settings, [tool_call("boom"), "Đã xử lý lỗi."])
    st = _run(agent)
    tm = next(m for m in st["messages"] if isinstance(m, ToolMessage))
    out = json.loads(tm.content)
    assert tm.status == "error" and out["ok"] is False and "RuntimeError" in out["error"]
    assert agent_result(st)["answer"] == "Đã xử lý lỗi."


@pytest.mark.parametrize("approve", [False, True])
def test_write_tool_requires_confirmation(mock_settings, approve):
    PUSHED.clear()
    agent, model, _ = _agent(mock_settings, [tool_call("push_data", payload="RFI-1"), "Xong."],
                             checkpointer=InMemorySaver())
    first = _run(agent, thread=f"w{approve}")
    pending = pending_confirms(first["__interrupt__"])
    assert pending == [{"confirm_id": pending[0]["confirm_id"], "tool": "push_data", "args": {"payload": "RFI-1"}}]
    assert PUSHED == []                                            # chưa xác nhận → chưa chạy
    st = agent.invoke(resume_command(pending[0]["confirm_id"], approve),
                      {"configurable": {"thread_id": f"w{approve}"}}, context=CTX)
    tm = next(m for m in st["messages"] if isinstance(m, ToolMessage))
    if approve:
        assert PUSHED == ["RFI-1"] and json.loads(tm.content)["pushed"] == "RFI-1"
    else:
        assert PUSHED == [] and "TỪ CHỐI" in tm.content and tm.status != "error"
    assert agent_result(st)["answer"] == "Xong."


def test_large_tool_result_is_offloaded(mock_settings):
    agent, _, boxes = _agent(mock_settings, [tool_call("big"), "Xong."], cfg_over={"tool_result_max_tokens": 100})
    st = _run(agent)
    tm = next(m for m in st["messages"] if isinstance(m, ToolMessage))
    out = json.loads(tm.content)
    assert out["truncated"] and out["full_result_file"].startswith("input/tool_big_")
    assert len(tm.content) < 1500
    full = json.loads(boxes["conv-1"].get_file(out["full_result_file"]))
    assert full["content"].count("dòng dữ liệu dài") == 3000


@pytest.mark.parametrize("threshold,expect_summary", [(500, True), (10**6, False)])
def test_summarization_threshold(mock_settings, threshold, expect_summary):
    history = []
    for i in range(30):
        history += [{"role": "user", "content": f"Câu hỏi {i} " + "nội dung " * 30},
                    {"role": "assistant", "content": f"Trả lời {i} " + "kết quả " * 30}]
    ctx = AgentContext("test_engineer", "conv-1", history=history)
    agent, model, _ = _agent(mock_settings, ["Xong."], cfg_over={"summarize_above_tokens": threshold})
    st = _run(agent, ctx=ctx)
    assert (model.summary_calls > 0) is expect_summary
    sent = model.calls[0]
    assert (len(sent) < 40) is expect_summary                     # phần cũ được thay bằng bản tóm tắt
    assert agent_result(st)["answer"] == "Xong."
