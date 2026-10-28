"""Unit N1 – Router (parse JSON, retry, fallback)."""
import pytest

from shared.llm import MockChatModel

from query.models import RouterOutput
from query.router import run_router
from tests.query.fakes import make_deps, router_json

pytestmark = pytest.mark.unit


def _deps(mock_settings, responses):
    llm = MockChatModel(responses=responses)
    return make_deps(mock_settings, llm=llm), llm


def test_parse_ok_with_code_fence(mock_settings):
    deps, llm = _deps(mock_settings, ["```json\n" + router_json(q="Tiết diện cáp S5?", project_mentions=["S5"],
                                                                 queries_vi=["tiết diện cáp"]) + "\n```"])
    out, fb = run_router(deps, "Tiết diện?", [], [], [])
    assert not fb and out.rewritten_question == "Tiết diện cáp S5?" and out.project_mentions == ["S5"]
    assert len(llm.calls) == 1


def test_bad_json_retry_then_fallback(mock_settings):
    deps, llm = _deps(mock_settings, ["không phải JSON", "{hỏng"])
    out, fb = run_router(deps, "Màu dây pha?", [], [], [])
    assert fb and len(llm.calls) == 2   # 1 lần + json_retry = 1
    assert out == RouterOutput.fallback("Màu dây pha?")
    assert out.project_action == "keep" and out.disciplines == [] and out.streams == []
    assert out.queries_vi == ["Màu dây pha?"]


def test_retry_succeeds(mock_settings):
    deps, llm = _deps(mock_settings, ["oops", router_json(q="ok", queries_vi=["a"])])
    out, fb = run_router(deps, "q", [], [], [])
    assert not fb and out.rewritten_question == "ok" and len(llm.calls) == 2


def test_invalid_enums_are_dropped(mock_settings):
    deps, _ = _deps(mock_settings, [router_json(q="q", disciplines=["hvac", "PLUMBING", "XYZ"],
                                                streams=["design_consultant", "FOO"], project_action="move",
                                                intent_type="??", queries_vi=[])])
    out, _ = run_router(deps, "q", [], [], [])
    assert out.disciplines == ["HVAC", "PLUMBING"] and out.streams == ["DESIGN_CONSULTANT"]
    assert out.project_action == "keep" and out.intent_type == "OTHER"
    assert out.queries_vi == ["q"]   # không có query nào → dùng câu hỏi viết lại


def test_history_in_prompt(mock_settings):
    deps, llm = _deps(mock_settings, [router_json(q="q")])
    hist = [{"role": "user", "content": "Gió tươi phòng gym S5?"}, {"role": "assistant", "content": "10 L/s.person"}]
    run_router(deps, "Còn lounge thì sao?", hist, ["S5"], [{"name": "S5", "aliases": ["BDD"]}])
    prompt = llm.calls[0][-1].content
    assert "Gió tươi phòng gym S5?" in prompt and "Còn lounge thì sao?" in prompt and "BDD" in prompt
