"""Unit – assistant graph (không còn Gate): mọi câu hỏi vào agent, stream token / token_reset, lịch sử giữa các lượt,
active_projects, file, xác nhận tool write."""
import json

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.services.agent.graph import (
    WAITING_CONFIRM, build_assistant_graph, pending_confirm_ids, stream_assistant_turn, stream_confirm,
)
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import ExecResult, SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.query.graph import turn_config
from tests.agent.fakes import ScriptedModel, tool_call
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
    return json.dumps({"ok": True})


push_data.metadata = {"access": "write"}


def _write_file(sbx, cmd):
    sbx.put_file("output/a.xlsx", b"PK")
    return ExecResult(0, "ok", "")


def _setup(mock_settings, script):
    deps = make_deps(mock_settings, llm=scripted_llm(BEND))
    manager = SandboxManager(lambda cid: MockSandbox(_write_file), 1800)
    model = ScriptedModel(script=script, calls=[])
    rt = build_runtime(deps, manager=manager, model=model, extra_tools=[push_data])
    return build_assistant_graph(deps, rt, InMemorySaver()), deps, model, manager


def _turn(graph, question, cid=None, **kw):
    events = list(stream_assistant_turn(graph, "test_engineer", question, cid, **kw))
    done = next(e["data"] for e in events if e["event"] == "done")
    return events, done


def _kinds(events):
    return [e["event"] for e in events]


def _answer(events):
    """Text hiển thị như giao diện: cộng token, token_reset thì xóa."""
    text = ""
    for e in events:
        if e["event"] == "token":
            text += e["data"]
        elif e["event"] == "token_reset":
            text = ""
    return text


def test_simple_question_goes_to_agent_and_streams(mock_settings):
    graph, deps, model, _ = _setup(mock_settings, [tool_call("search_project_docs", question="Bán kính uốn cáp?"),
                                                   "Bán kính uốn 6 lần [1]."])
    events, done = _turn(graph, "Bán kính uốn cáp?")
    assert "route" not in _kinds(events) and "route" not in done
    assert [e["data"]["tool"] for e in events if e["event"] == "tool_start"] == ["search_project_docs"]
    assert _answer(events) == "Bán kính uốn 6 lần [1]." and "token_reset" not in _kinds(events)
    assert [c["n"] for c in next(e["data"] for e in events if e["event"] == "citations")] == [1]
    assert done["status"] == "OK" and done["active_projects"] == [S5]
    assert not [c for c in deps.llm_main.calls                                         # không chạy N7 của pipeline
                if str(c[0].content).startswith("Bạn là trợ lý tra cứu")]


def test_text_before_tool_call_is_reset(mock_settings):
    lead = AIMessage("Tôi sẽ tra cứu tài liệu.", tool_calls=[{"name": "search_project_docs",
                                                              "args": {"question": "Bán kính uốn?"}, "id": "c1"}])
    graph, _, _, _ = _setup(mock_settings, [lead, "Bán kính uốn 6 lần [1]."])
    events, done = _turn(graph, "Bán kính uốn cáp?")
    k = _kinds(events)
    assert k.index("token") < k.index("token_reset") < k.index("tool_start")
    assert _answer(events) == "Bán kính uốn 6 lần [1]."
    msgs = graph.get_state(turn_config(done["conversation_id"])).values["messages"]
    assert msgs[-1]["content"] == "Bán kính uốn 6 lần [1]."


def test_agent_events_files_and_citations(mock_settings):
    script = [tool_call("search_project_docs", question="Bán kính uốn cáp > 25mm2?"),
              tool_call("run_code", code="print(1)"), "Đã xuất a.xlsx theo mục 5.7 [1]."]
    graph, _, model, _ = _setup(mock_settings, script)
    events, done = _turn(graph, "Xuất excel bán kính uốn")
    k = _kinds(events)
    assert k.count("tool_start") == 2 and k.count("tool_end") == 2
    assert [e["data"]["tool"] for e in events if e["event"] == "tool_start"] == ["search_project_docs", "run_code"]
    assert all(e["data"]["ok"] for e in events if e["event"] == "tool_end")
    assert _answer(events) == "Đã xuất a.xlsx theo mục 5.7 [1]."
    cites = next(e["data"] for e in events if e["event"] == "citations")
    assert [c["n"] for c in cites] == [1]
    f = next(e["data"] for e in events if e["event"] == "file")
    assert f["name"] == "a.xlsx" and f["size"] == 2 and f["url"].endswith("/files/a.xlsx?user_id=test_engineer")
    assert done["status"] == "OK" and done["active_projects"] == [S5]


def test_history_across_turns(mock_settings):
    seen = []

    def step(messages):
        seen.append([type(m).__name__ for m in messages])
        return f"Trả lời {len(seen)}."

    graph, deps, _, _ = _setup(mock_settings, [step])
    _, d1 = _turn(graph, "Bán kính uốn cáp?")
    cid = d1["conversation_id"]
    _turn(graph, "Xuất excel bảng trên", cid)
    _turn(graph, "Tiết diện tối thiểu?", cid)
    assert seen[0] == ["SystemMessage", "HumanMessage"]
    assert seen[2] == ["SystemMessage", *["HumanMessage", "AIMessage"] * 2, "HumanMessage"]   # thấy 2 lượt trước
    msgs = graph.get_state(turn_config(cid)).values["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] * 3
    assert msgs[3]["content"] == "Trả lời 2." and "route" not in msgs[3]
    assert not any(m.get("tool_calls") for m in msgs)                                   # không có tool call nội bộ
    assert deps.repo.get_conversation(cid)["title"] == "Bán kính uốn cáp?"


def test_search_updates_active_projects(mock_settings):
    graph, _, _, _ = _setup(mock_settings, [tool_call("search_project_docs", question="Bán kính uốn?"), "Xong [1]."])
    _, d = _turn(graph, "Xuất excel")
    assert d["active_projects"] == [S5]
    assert graph.get_state(turn_config(d["conversation_id"])).values["active_projects"] == [S5]


@pytest.mark.parametrize("approve", [False, True])
def test_write_tool_confirm_through_assistant_graph(mock_settings, approve):
    PUSHED.clear()
    graph, _, model, _ = _setup(mock_settings, [tool_call("push_data", payload="RFI-1"), "Đã xử lý."])
    events, done = _turn(graph, "Đẩy dữ liệu sang hệ thống ngoài")
    cid = done["conversation_id"]
    confirm = next(e["data"] for e in events if e["event"] == "confirm_required")
    assert confirm["tool"] == "push_data" and confirm["args"] == {"payload": "RFI-1"}
    assert done["status"] == WAITING_CONFIRM and PUSHED == []
    assert pending_confirm_ids(graph, cid) == [confirm["confirm_id"]]
    assert graph.get_state(turn_config(cid)).values.get("messages") in (None, [])       # chưa lưu lượt

    ev2 = list(stream_confirm(graph, cid, confirm["confirm_id"], approve))
    assert PUSHED == (["RFI-1"] if approve else [])
    assert next(e["data"] for e in ev2 if e["event"] == "done")["status"] == "OK"
    assert _answer(ev2) == "Đã xử lý."
    assert next(e["data"] for e in ev2 if e["event"] == "tool_end")["tool"] == "push_data"
    assert pending_confirm_ids(graph, cid) == []
    msgs = graph.get_state(turn_config(cid)).values["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == "Đã xử lý."
    assert len(model.calls) == 2


def test_files_before_confirm_are_reported(mock_settings):
    PUSHED.clear()
    script = [tool_call("run_code", code="print(1)"), tool_call("push_data", payload="x"), "Xong."]
    graph, _, _, _ = _setup(mock_settings, script)
    events, done = _turn(graph, "Xuất rồi đẩy")
    confirm = next(e["data"] for e in events if e["event"] == "confirm_required")
    ev2 = list(stream_confirm(graph, done["conversation_id"], confirm["confirm_id"], True))
    assert [e["data"]["name"] for e in ev2 if e["event"] == "file"] == ["a.xlsx"]       # file tạo trước khi chờ vẫn được báo


def test_new_turn_after_abandoned_confirm_starts_clean(mock_settings):
    PUSHED.clear()
    graph, _, _, _ = _setup(mock_settings, [tool_call("push_data", payload="x"), "Trả lời lượt mới."])
    _, d = _turn(graph, "Đẩy dữ liệu")
    cid = d["conversation_id"]
    events, d2 = _turn(graph, "Bán kính uốn cáp?", cid)
    assert d2["status"] == "OK" and PUSHED == [] and _answer(events) == "Trả lời lượt mới."


def test_parallel_write_tools_are_confirmed_one_by_one(mock_settings):
    PUSHED.clear()
    calls = [{"name": "push_data", "args": {"payload": p}, "id": f"c{p}"} for p in ("A", "B")]
    graph, _, _, _ = _setup(mock_settings, [AIMessage("", tool_calls=calls), "Xong."])
    events, done = _turn(graph, "Đẩy 2 mục")
    cid = done["conversation_id"]
    first = [e["data"] for e in events if e["event"] == "confirm_required"]
    assert sorted(c["args"]["payload"] for c in first) == ["A", "B"]
    by_payload = {c["args"]["payload"]: c["confirm_id"] for c in first}
    ev2 = list(stream_confirm(graph, cid, by_payload["A"], True))             # B vẫn chờ
    assert PUSHED == ["A"]
    assert [e["data"]["args"]["payload"] for e in ev2 if e["event"] == "confirm_required"] == ["B"]
    ev3 = list(stream_confirm(graph, cid, by_payload["B"], False))
    assert PUSHED == ["A"] and next(e["data"] for e in ev3 if e["event"] == "done")["status"] == "OK"
