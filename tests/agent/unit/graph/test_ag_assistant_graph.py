"""Unit – assistant graph: phân tuyến, lịch sử hợp nhất lookup → agent → lookup, active_projects, file, xác nhận tool write."""
import json

import pytest
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from app.providers.llm import MockChatModel
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


def _gate_aware_llm(answer="Bán kính uốn 6 lần [1]."):
    """Fast/main LLM giả: Gate → agent khi câu hỏi có 'xuất' hoặc 'đẩy'; còn lại như scripted_llm của Query Pipeline."""
    base = scripted_llm(BEND, answer=answer)

    def handler(messages):
        if str(messages[0].content).startswith("Bạn phân tuyến"):
            q = str(messages[-1].content).split("Yêu cầu:")[-1].lower()
            return json.dumps({"route": "agent" if ("xuất" in q or "đẩy" in q) else "lookup", "reason": "t"})
        return base.handler(messages)

    return MockChatModel(handler=handler, calls=[])


def _write_file(sbx, cmd):
    sbx.put_file("output/a.xlsx", b"PK")
    return ExecResult(0, "ok", "")


def _setup(mock_settings, script):
    deps = make_deps(mock_settings, llm=_gate_aware_llm())
    manager = SandboxManager(lambda cid: MockSandbox(_write_file), 1800)
    model = ScriptedModel(script=script, calls=[])
    rt = build_runtime(deps, manager=manager, model=model, mcp_tools=[push_data])
    return build_assistant_graph(deps, rt, InMemorySaver()), deps, model, manager


def _turn(graph, question, cid=None, **kw):
    events = list(stream_assistant_turn(graph, "test_engineer", question, cid, **kw))
    done = next(e["data"] for e in events if e["event"] == "done")
    return events, done


def _kinds(events):
    return [e["event"] for e in events]


def test_lookup_route_keeps_events_and_no_agent(mock_settings):
    graph, _, model, _ = _setup(mock_settings, ["KHÔNG ĐƯỢC GỌI"])
    events, done = _turn(graph, "Bán kính uốn cáp?")
    assert next(e["data"] for e in events if e["event"] == "route") == {"route": "lookup"}
    assert {"status", "token", "citations", "done"} <= set(_kinds(events))
    assert done["status"] == "OK" and done["route"] == "lookup" and model.calls == []
    tokens = "".join(e["data"] for e in events if e["event"] == "token")
    assert tokens == "Bán kính uốn 6 lần [1]."


def test_agent_route_events_files_and_citations(mock_settings):
    script = [tool_call("search_project_docs", question="Bán kính uốn cáp > 25mm2?"),
              tool_call("run_code", code="print(1)"), "Đã xuất a.xlsx theo mục 5.7 [1]."]
    graph, _, model, _ = _setup(mock_settings, script)
    events, done = _turn(graph, "Xuất excel bán kính uốn")
    k = _kinds(events)
    assert next(e["data"] for e in events if e["event"] == "route") == {"route": "agent"}
    assert k.count("tool_start") == 2 and k.count("tool_end") == 2
    assert [e["data"]["tool"] for e in events if e["event"] == "tool_start"] == ["search_project_docs", "run_code"]
    assert all(e["data"]["ok"] for e in events if e["event"] == "tool_end")
    assert "".join(e["data"] for e in events if e["event"] == "token") == "Đã xuất a.xlsx theo mục 5.7 [1]."
    cites = next(e["data"] for e in events if e["event"] == "citations")
    assert [c["n"] for c in cites] == [1]
    f = next(e["data"] for e in events if e["event"] == "file")
    assert f["name"] == "a.xlsx" and f["size"] == 2 and f["url"].endswith("/files/a.xlsx?user_id=test_engineer")
    assert done["status"] == "OK" and done["active_projects"] == [S5]


def test_unified_history_lookup_agent_lookup(mock_settings):
    seen = []

    def agent_step(messages):
        seen.append([type(m).__name__ for m in messages])
        return "Đã xuất file."

    graph, deps, _, _ = _setup(mock_settings, [agent_step])
    _, d1 = _turn(graph, "Bán kính uốn cáp?")
    cid = d1["conversation_id"]
    _, d2 = _turn(graph, "Xuất excel bảng trên", cid)
    _, d3 = _turn(graph, "Tiết diện tối thiểu?", cid)
    assert (d2["route"], d3["route"]) == ("agent", "lookup")
    assert seen[0] == ["SystemMessage", "HumanMessage", "AIMessage", "HumanMessage"]   # agent thấy lượt lookup
    msgs = graph.get_state(turn_config(cid)).values["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] * 3
    assert msgs[3]["content"] == "Đã xuất file." and msgs[3]["route"] == "agent"
    assert not any(m.get("tool_calls") for m in msgs)                                   # không có tool call nội bộ
    router_calls = [str(c[-1].content) for c in deps.llm_fast.calls if str(c[0].content).startswith("Bạn là bộ định")]
    assert "Đã xuất file." in router_calls[-1]                                          # lookup thấy lượt agent
    assert graph.get_state(turn_config(cid)).values["active_projects"] == [S5]
    assert deps.repo.get_conversation(cid)["title"] == "Bán kính uốn cáp?"


def test_agent_search_updates_active_projects_for_next_lookup(mock_settings):
    graph, _, _, _ = _setup(mock_settings, [tool_call("search_project_docs", question="Bán kính uốn?"), "Xong [1]."])
    _, d = _turn(graph, "Xuất excel", force_route="agent")
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
    assert "".join(e["data"] for e in ev2 if e["event"] == "token") == "Đã xử lý."
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
    _, d2 = _turn(graph, "Bán kính uốn cáp?", cid)
    assert d2["route"] == "lookup" and d2["status"] == "OK" and PUSHED == []
