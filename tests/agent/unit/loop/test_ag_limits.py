"""Unit – lưới an toàn vòng lặp (loop/limits.py): bước trả lời ép buộc (không tool, timeout ngắn, lỗi → thông báo cố
định), đóng tra cứu theo số bước tra, lời nhắc chỉ ở bước ngay sau khi đóng / bị chặn, thời gian chạy thực."""
import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.agent.loop.agent import agent_result
from app.services.agent.loop.limits import BUDGET_STATUS, LLM_ERROR, LimitsMiddleware, consecutive_blocked_steps
from app.services.agent.state import AgentContext
from tests.agent.fakes import tool_call
from tests.agent.unit.loop.test_ag_agent_loop import _agent, _run

pytestmark = pytest.mark.unit

Q = "Bán kính uốn cáp > 25mm2?"
NOTE = "[Hệ thống]"


def search(i: int | str, q: str = Q) -> dict:
    return {"name": "search_project_docs", "args": {"question": q}, "id": f"s{i}"}


def ai(*calls: dict) -> AIMessage:
    return AIMessage("", tool_calls=list(calls))


def answer_if_note(text: str = "Trả lời từ dữ liệu đã có [1]."):
    """Bước LLM: thấy lời nhắc ép trả lời ở cuối → trả lời; không thấy → vẫn cố tra (LLM không nghe prompt)."""
    def step(messages):
        last = messages[-1]
        if isinstance(last, HumanMessage) and "Đã chạm giới hạn" in str(last.content):
            return AIMessage(text)
        return ai(search("x"))
    return step


def tool_msgs(st, name="search_project_docs"):
    return [m for m in st["messages"] if isinstance(m, ToolMessage) and m.name == name]


def budget_msgs(st):
    return [m for m in st["messages"] if isinstance(m, ToolMessage) and BUDGET_STATUS in str(m.content)]


def has_note(messages) -> bool:
    return isinstance(messages[-1], HumanMessage) and NOTE in str(messages[-1].content)


def request(state: dict, messages=None, tools=None) -> ModelRequest:
    return ModelRequest(model=None, messages=messages or [HumanMessage("q")], system_message=None, tool_choice=None,
                        tools=tools if tools is not None else [object()], response_format=None, state=state,
                        runtime=None, model_settings={"a": 1})


def boom(_req):
    raise TimeoutError("429 RESOURCE_EXHAUSTED")


# ------------------------------------------------------------------ bước trả lời ép buộc

def test_forced_answer_uses_data_instead_of_empty_stop(mock_settings):
    script = [ai(search(1)), tool_call("list_files"), answer_if_note("Bán kính uốn 6 lần đường kính [1].")]
    agent, model, _ = _agent(mock_settings, script, cfg_over={"max_steps": 3, "max_search_rounds": 0})
    st = _run(agent)
    res = agent_result(st)
    assert len(model.calls) == 3 and "giới hạn 3 bước" in model.calls[2][-1].content
    assert res["answer"] == "Bán kính uốn 6 lần đường kính [1]."
    assert res["stop_reason"] and [c["n"] for c in res["citations"]] == [1]
    assert len(model.bound) == 2                                                     # bước ép không gửi tool
    assert not any(isinstance(m, HumanMessage) and NOTE in str(m.content) for m in st["messages"])  # không lưu


def test_forced_step_short_timeout_no_tools_and_strips_calls():
    mw = LimitsMiddleware(25, 3, 300, forced_answer_timeout_s=60)
    seen = {}

    def handler(req):
        seen["req"] = req
        return ModelResponse(result=[AIMessage("", tool_calls=[search("z")])])

    req = request({"force_answer": "đã đạt giới hạn"})
    out = mw.wrap_model_call(req, handler).result[0]
    r = seen["req"]
    assert r.tools == [] and r.model_settings == {"a": 1, "timeout": 60, "max_retries": 1}
    assert "Đã chạm giới hạn" in r.messages[-1].content and len(req.messages) == 1    # không sửa request gốc
    assert not out.tool_calls and "Tôi phải dừng vì đã đạt giới hạn" in out.text


def test_forced_step_llm_error_returns_stop_text():
    mw = LimitsMiddleware(25, 3, 300, forced_answer_timeout_s=60)
    msgs = [HumanMessage("q"), AIMessage("Đã tra được A [1]."), HumanMessage("tiếp")]
    out = mw.wrap_model_call(request({"force_answer": "vượt thời gian tối đa 300 giây"}, msgs), boom)
    assert isinstance(out, AIMessage) and "Tôi phải dừng vì vượt thời gian" in out.text and "Đã tra được A" in out.text


def test_normal_step_llm_error_with_tool_data_falls_back_to_forced_answer():
    mw = LimitsMiddleware(25, 3, 300)
    msgs = [HumanMessage("q"), ai(search(1)), ToolMessage('{"ok": true}', tool_call_id="s1", name="search_project_docs")]
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise TimeoutError("timeout")
        return ModelResponse(result=[AIMessage("Trả lời từ dữ liệu [1].")])

    out = mw.wrap_model_call(request({}, msgs), handler)
    assert isinstance(out, ExtendedModelResponse) and out.command.update == {"stop_reason": LLM_ERROR}
    assert out.model_response.result[0].text == "Trả lời từ dữ liệu [1]." and calls[1].tools == []
    # ép cũng lỗi → thông báo cố định, không ném lỗi ra ngoài
    out2 = mw.wrap_model_call(request({}, msgs), boom)
    assert "Tôi phải dừng vì lỗi gọi mô hình" in out2.model_response.result[0].text


def test_normal_step_llm_error_without_tool_data_is_raised():
    with pytest.raises(TimeoutError):
        LimitsMiddleware(25, 3, 300).wrap_model_call(request({}), boom)


def test_forced_answer_in_agent_when_llm_errors(mock_settings):
    def flaky(messages):
        if isinstance(messages[-1], ToolMessage):
            raise TimeoutError("429")
        return ai(search(1))
    agent, model, _ = _agent(mock_settings, [flaky], cfg_over={"max_search_rounds": 0})
    res = agent_result(_run(agent))
    assert res["stop_reason"] == LLM_ERROR and res["answer"].startswith("Tôi phải dừng vì lỗi gọi mô hình")


# ------------------------------------------------------------------ đóng tra cứu theo số bước tra

def test_search_rounds_close_drops_tool_and_blocks_extra_call(mock_settings):
    script = [ai(search(1)), ai(search(2, "tiết diện tối thiểu")), ai(search(3, "màu pha")), "Trả lời [1]."]
    agent, model, _ = _agent(mock_settings, script, cfg_over={"max_search_rounds": 2})
    st = _run(agent)
    real = [m for m in tool_msgs(st) if BUDGET_STATUS not in m.content]
    assert len(real) == 2 and len(budget_msgs(st)) == 1 and budget_msgs(st)[0].status != "error"
    assert "search_project_docs" in model.bound[1] and "search_project_docs" not in model.bound[2]
    assert "Đã hết lượt tra" in model.calls[2][-1].content and "đã tra 2 bước" in model.calls[2][-1].content
    assert has_note(model.calls[3])                                       # bước ngay sau khi call bị chặn
    blocked = budget_msgs(st)[0].content
    assert "chưa tra được do giới hạn" in blocked and "không nói là tài liệu không có" in blocked
    assert agent_result(st)["stop_reason"] is None


def test_parallel_searches_count_as_one_round(mock_settings):
    script = [ai(search(1), search(2, "tiết diện"), search(3, "màu pha")), ai(search(4, "bảo hành")), "Xong [1]."]
    agent, model, _ = _agent(mock_settings, script, cfg_over={"max_search_rounds": 2})
    st = _run(agent)
    assert len(tool_msgs(st)) == 4 and budget_msgs(st) == []
    assert "Đã hết lượt tra" in model.calls[2][-1].content


def test_note_only_right_after_close_not_every_step(mock_settings):
    """Đóng tra cứu rồi agent tiếp tục làm file: lời nhắc chỉ ở bước ngay sau khi đóng, tool search vẫn bị bỏ."""
    script = [ai(search(1)), tool_call("load_skill", name="xlsx-export"), tool_call("run_code", code="print(1)"),
              tool_call("list_files"), "Xong."]
    agent, model, _ = _agent(mock_settings, script, cfg_over={"max_search_rounds": 1})
    _run(agent)
    assert [has_note(c) for c in model.calls] == [False, True, False, False, False]
    assert all("search_project_docs" not in b for b in model.bound[1:])


def test_repeated_blocked_steps_force_answer(mock_settings):
    script = [ai(search(1)), ai(search(2)), ai(search(3)), answer_if_note("Trả lời [1].")]
    agent, model, _ = _agent(mock_settings, script, cfg_over={"max_search_rounds": 1})
    st = _run(agent)
    assert len(budget_msgs(st)) == 2 and len(model.calls) == 4
    assert "Đã chạm giới hạn" in model.calls[3][-1].content
    res = agent_result(st)
    assert res["answer"] == "Trả lời [1]." and res["stop_reason"]
    assert consecutive_blocked_steps(st["messages"][:-1]) == 2


def test_no_rounds_limit_means_unlimited(mock_settings):
    script = [ai(search(1)), ai(search(2)), ai(search(3)), ai(search(4)), "Xong [1]."]
    agent, model, _ = _agent(mock_settings, script, cfg_over={"max_search_rounds": 0})
    st = _run(agent)
    assert len(tool_msgs(st)) == 4 and budget_msgs(st) == []
    assert not any(has_note(c) for c in model.calls)


def test_from_cfg_defaults_to_unlimited():
    mw = LimitsMiddleware.from_cfg({"max_steps": 5, "max_consecutive_tool_errors": 2, "max_wall_time_s": 10})
    assert (mw.max_rounds, mw.forced_timeout) == (0, 0)


# ------------------------------------------------------------------ thời gian chạy thực

class _Rt:
    def __init__(self, ctx):
        self.context = ctx


def test_active_time_excludes_wait_for_confirmation():
    t = {"now": 0.0}
    mw = LimitsMiddleware(25, 3, 300, clock=lambda: t["now"])
    ctx = AgentContext("u", "c")
    st = mw.before_agent({}, _Rt(ctx))
    t["now"] = 20.0
    upd = mw.before_model({**st, "messages": []}, _Rt(ctx))
    assert upd["active_s"] == 20.0
    t["now"] = 1000.0                                                # chờ người dùng xác nhận 980s → resume
    resumed = AgentContext("u", "c")                                  # assistant graph tạo context mới khi resume
    upd2 = mw.before_model({**st, **upd, "messages": []}, _Rt(resumed))
    assert upd2["active_s"] == 20.0 and "force_answer" not in upd2
    t["now"] = 1010.0
    assert mw.before_model({**st, **upd2, "messages": []}, _Rt(resumed))["active_s"] == 30.0


# ------------------------------------------------------------------ bước tra toàn NOT_FOUND không tính vào trần

def _round(i: int, status: str) -> list:
    import json

    return [ai(search(i)), ToolMessage(json.dumps({"ok": True, "status": status}), tool_call_id=f"s{i}",
                                       name="search_project_docs")]


def test_not_found_rounds_do_not_count_but_total_cap_holds():
    from app.services.agent.loop.limits import search_rounds

    assert search_rounds([*_round(1, "NOT_FOUND"), *_round(2, "OK")]) == (1, 2)
    assert search_rounds([*_round(1, "PARTIAL"), ai(search(9))]) == (1, 1)          # call chưa có kết quả: chưa tính
    mw = LimitsMiddleware(25, 3, 300, max_search_rounds=2, max_search_rounds_total=4)
    st = lambda msgs: {"messages": msgs, "step_count": len(msgs) // 2}  # noqa: E731
    # 1 bước NOT_FOUND + 1 bước OK → còn được tra (A4: bảng lúc trúng lúc trượt)
    assert "search_closed" not in mw.before_model(st([*_round(1, "NOT_FOUND"), *_round(2, "OK")]), None)
    assert mw.before_model(st([*_round(1, "OK"), *_round(2, "OK")]), None)["search_closed"] == "đã tra 2 bước"
    four = [*_round(1, "NOT_FOUND"), *_round(2, "NOT_FOUND"), *_round(3, "NOT_FOUND"), *_round(4, "OK")]
    assert mw.before_model(st(four), None)["search_closed"] == "đã tra 4 bước"       # trần tổng
