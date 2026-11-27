"""Unit – trần vòng lặp qua assistant graph: nhiều lượt (đếm theo lượt), resume sau xác nhận tool write (không đếm đôi),
bước trả lời ép buộc khi đã mở nguồn ngoài (không gắn lại tool MCP)."""
import copy
import json

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.providers.llm import MockChatModel
from app.services.agent.config import agent_cfg
from app.services.agent.graph import build_assistant_graph, stream_assistant_turn, stream_confirm
from app.services.agent.mcp.catalog import SourceUnavailable, source_entry
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from tests.agent.fakes import ScriptedModel, tool_call
from tests.agent.unit.graph.test_ag_assistant_graph import PUSHED, _setup, _turn
from tests.query.fakes import make_deps

pytestmark = pytest.mark.unit

SKIPPED = "bỏ qua (hết lượt tra)"


def s(i, q="Bán kính uốn cáp > 25mm2?"):
    return {"name": "search_project_docs", "args": {"question": q}, "id": f"s{i}"}


def ends(events):
    return [(e["data"]["tool"], e["data"]["summary"]) for e in events if e["event"] == "tool_end"]


def done(events):
    return next(e["data"] for e in events if e["event"] == "done")


def test_second_turn_has_fresh_budget(mock_settings):
    """Lượt 1 dùng hết 2 bước tra; lượt 2 lại được tra 2 bước (không bị chặn oan)."""
    script = [AIMessage("", tool_calls=[s(1)]), AIMessage("", tool_calls=[s(2)]), "A1 [1].",
              AIMessage("", tool_calls=[s(3)]), AIMessage("", tool_calls=[s(4)]), "A2 [1]."]
    graph, _, model, _ = _setup(mock_settings, script)
    ev1, d1 = _turn(graph, "q1")
    ev2, d2 = _turn(graph, "q2", d1["conversation_id"])
    assert all(x != SKIPPED for _, x in ends(ev1) + ends(ev2))
    assert len(ends(ev1)) == len(ends(ev2)) == 2 and d1["status"] == d2["status"] == "OK"
    assert ["search_project_docs" in b for b in model.bound] == [True, True, False, True, True, False]


def test_resume_after_confirm_does_not_double_count(mock_settings):
    """Bước 1: 2 lần tra + tool write (chờ xác nhận). Resume: bước 2 vẫn được tra (2 bước), bước 3 bị chặn."""
    PUSHED.clear()
    script = [AIMessage("", tool_calls=[s(1), s(2), {"name": "push_data", "args": {"payload": "x"}, "id": "p1"}]),
              AIMessage("", tool_calls=[s(3)]), AIMessage("", tool_calls=[s(4)]), "Xong [1]."]
    graph, _, model, _ = _setup(mock_settings, script)
    ev, d = _turn(graph, "q")
    conf = next(e["data"] for e in ev if e["event"] == "confirm_required")
    ev2 = list(stream_confirm(graph, d["conversation_id"], conf["confirm_id"], True))
    assert PUSHED == ["x"]
    after = [x for t, x in ends(ev2) if t == "search_project_docs"][-2:]       # s3, s4
    assert after[0] != SKIPPED and after[1] == SKIPPED
    assert len(model.calls) == 4


# ------------------------------------------------------------------ bước ép khi đã mở nguồn ngoài

@tool("kho_ton")
def kho_ton(ten: str) -> str:
    """Tồn kho vật tư (giả lập tool connector đọc)."""
    return json.dumps({"ok": True, "ton": 3})


kho_ton.metadata = {"access": "read", "mcp_server": "kho"}


class Hub:
    def catalog(self, user_id):
        return [source_entry("kho", "Kho vật tư", "Tồn kho vật tư", "connected")]

    def source_tools(self, user_id, name):
        if name != "kho":
            raise SourceUnavailable("không có")
        return [kho_ton]


def test_forced_answer_after_opening_source_has_no_tools(mock_settings):
    def answer(messages):
        last = messages[-1]
        if isinstance(last, HumanMessage) and "Đã chạm giới hạn" in str(last.content):
            return AIMessage("Tồn 3 (theo hệ thống kho).")
        return tool_call("kho_ton", ten="ống")

    cfg = copy.deepcopy(agent_cfg())
    cfg["agent"]["max_steps"] = 3
    deps = make_deps(mock_settings, llm=MockChatModel(responses=["x"], calls=[]))
    model = ScriptedModel(script=[tool_call("open_source_tools", name="kho"), tool_call("kho_ton", ten="ống"),
                                  answer], calls=[])
    rt = build_runtime(deps, cfg=cfg, manager=SandboxManager(lambda cid: MockSandbox(), 1800), model=model,
                       yaml_tools=[], mcp=Hub())
    graph = build_assistant_graph(deps, rt, InMemorySaver())
    ev = list(stream_assistant_turn(graph, "u1", "tồn kho ống?"))
    assert [t for t, _ in ends(ev)] == ["open_source_tools", "kho_ton"]
    assert len(model.calls) == 3
    assert len(model.bound) == 2 and "kho_ton" in model.bound[1]                 # bước 3 (ép) không bind tool nào
    assert done(ev)["status"] == "PARTIAL"
    text = "".join(e["data"] for e in ev if e["event"] == "token")
    assert text.endswith("Tồn 3 (theo hệ thống kho).")
