"""Unit – G0. Gate: JSON hợp lệ, đính kèm/force_route không gọi LLM, JSON lỗi → retry → fallback lookup."""
import pytest

from app.providers.llm import MockChatModel
from app.services.agent.config import agent_cfg
from app.services.agent.gate import run_gate
from app.services.agent.gate.gate import external_text, history_text

pytestmark = pytest.mark.unit

CFG = agent_cfg()["gate"]
LOOKUP = '{"route": "lookup", "reason": "tra cứu"}'
AGENT = '{"route": "agent", "reason": "xuất file"}'


def _llm(*responses):
    return MockChatModel(responses=list(responses), calls=[])


@pytest.mark.parametrize("question,resp,route", [
    ("Bán kính uốn cong tối thiểu của cáp là bao nhiêu?", LOOKUP, "lookup"),
    ("Xuất excel bảng gió tươi S5", AGENT, "agent"),
    ("Đẩy bảng này sang hệ thống Trimble", f"```json\n{AGENT}\n```", "agent"),
])
def test_routes(question, resp, route):
    llm = _llm(resp)
    out = run_gate(llm, CFG, question, [])
    assert out.route == route and len(llm.calls) == 1
    assert question in str(llm.calls[0][-1].content)


def test_attachment_goes_to_agent_without_llm():
    llm = _llm(LOOKUP)
    assert run_gate(llm, CFG, "điền vào mẫu", [], attachments=["mau.xlsx"]).route == "agent"
    assert llm.calls == []


def test_force_route_wins():
    llm = _llm(AGENT)
    assert run_gate(llm, CFG, "x", [], attachments=["a.xlsx"], force_route="lookup").route == "lookup"
    assert run_gate(llm, CFG, "x", [], force_route="agent").route == "agent"
    assert llm.calls == []


def test_bad_json_retries_once_then_fallback_lookup():
    llm = _llm("không phải json", '{"route": "bogus"}', AGENT)
    out = run_gate(llm, CFG, "x", [])
    assert out.route == "lookup" and out.reason == "fallback" and len(llm.calls) == 2


def test_retry_recovers():
    llm = _llm("hỏng", AGENT)
    assert run_gate(llm, CFG, "x", []).route == "agent" and len(llm.calls) == 2


def test_external_systems_in_prompt():
    """Gate thấy hệ thống ngoài đã kết nối (tên + tool) để chuyển câu hỏi về dữ liệu ở đó sang agent."""
    llm = _llm(AGENT)
    external = [{"name": "ai_nhansu", "label": "Nhân sự HAWEE",
                 "tools": [{"name": "find_employee", "description": "Tra cứu nhân viên theo tên"}]}]
    run_gate(llm, CFG, "Mã nhân viên của Nguyễn Văn A?", [], external=external)
    prompt = str(llm.calls[0][-1].content)
    assert "ai_nhansu (Nhân sự HAWEE)" in prompt and "find_employee: Tra cứu nhân viên theo tên" in prompt
    run_gate(_llm(LOOKUP), CFG, "x", [])   # không có hệ thống ngoài → "(không có)"
    assert external_text(None) == "(không có)"


def test_history_marks_agent_turn_and_files():
    msgs = [{"role": "user", "content": "xuất excel"},
            {"role": "assistant", "content": "Đã tạo", "route": "agent", "files": [{"name": "a.xlsx", "size": 1}]}]
    text = history_text(msgs, 6)
    assert "[tác vụ agent]" in text and "a.xlsx" in text
    assert history_text([], 6) == "(chưa có)"
