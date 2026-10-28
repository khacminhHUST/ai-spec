"""Unit N6 – Grader (regex + LLM), rẽ nhánh D/E/F, Targeted Fetch."""
import pytest

from query.grader import NONE_MARK, QUERY_PREFIX, grade, regex_missing, route_after_grade, targeted_fetch
from query.resolver import build_profile
from tests.query.fakes import S5, make_deps, scripted_llm, router_json

pytestmark = pytest.mark.unit
TM = "doc_11833346273d"
SPEC = "doc_bd42fa85440e"


def _block(n, doc_id, key, vi, is_reference=False):
    return {"n": n, "doc_id": doc_id, "section_key": key, "vi": vi, "en": None, "is_reference": is_reference,
            "unresolved_refs": [], "title": key, "project_id": S5, "stream": "DESIGN_CONSULTANT"}


@pytest.fixture
def deps(mock_settings):
    return make_deps(mock_settings)


def test_regex_detects_missing_table(deps):
    ctx = [_block(1, TM, "part_i__sec_4_2", "Lưu lượng gió tươi xem Bảng 4.3 bên dưới.")]
    assert regex_missing(deps, ctx) == ["table_4_3"]


def test_regex_ignores_present_or_unknown_refs(deps):
    ctx = [_block(1, TM, "part_i__sec_4_2", "Xem Bảng 4.3 và Bảng 9.9."), _block(2, TM, "table_4_3", "...", True)]
    assert regex_missing(deps, ctx) == []   # 4.3 đã có, 9.9 không có trong hệ thống


def test_regex_only_checks_primary_blocks(deps):
    ctx = [_block(1, SPEC, "sec_5_23", "bảo hành"), _block(2, TM, "x", "Xem Bảng 4.3", True)]
    assert regex_missing(deps, ctx) == []


@pytest.mark.parametrize("missing,hop,expected", [
    ([], 0, "OK"), ([], 1, "OK"),                                   # D
    (["table_4_3"], 1, "PARTIAL"), ([QUERY_PREFIX + "x"], 1, "PARTIAL"),  # E
    ([NONE_MARK + "x"], 1, "NOT_FOUND"),
    (["table_4_3"], 0, "FETCH"), ([NONE_MARK + "x"], 0, "FETCH"),   # F
])
def test_route_after_grade(missing, hop, expected):
    assert route_after_grade(missing, hop, 1) == expected


def test_llm_check_only_below_threshold(mock_settings):
    llm = scripted_llm(router_json(), grader='{"verdict": "PARTIAL", "missing_query": "gió thải toilet"}')
    deps = make_deps(mock_settings, llm=llm)
    ctx = [_block(1, SPEC, "sec_5_23", "bảo hành 24 tháng")]
    assert grade(deps, "q", ctx, top1=0.9) == [] and len(llm.calls) == 0
    assert grade(deps, "q", ctx, top1=0.4) == [QUERY_PREFIX + "gió thải toilet"] and len(llm.calls) == 1


def test_llm_check_bad_json_is_enough(mock_settings):
    deps = make_deps(mock_settings, llm=scripted_llm(router_json(), grader="không phải json"))
    assert grade(deps, "q", [_block(1, SPEC, "sec_5_23", "x")], top1=0.1) == []


def test_targeted_fetch_by_id_and_query(deps):
    state = {
        "missing": ["table_4_3", QUERY_PREFIX + "bán kính uốn cong cáp"],
        "project_profiles": {S5: build_profile(deps, S5)},
        "router": {"rewritten_question": "bán kính uốn cong cáp", "queries_vi": ["x"], "exact_keywords": []},
        "context": [_block(1, TM, "part_i__sec_4_2", "Bảng 4.3")],
        "branches": [{"project_id": S5, "stream": "DESIGN_CONSULTANT", "lang": "vi"}],
        "reranked": [], "hop_count": 0,
    }
    out = targeted_fetch(deps, state)
    assert out["hop_count"] == 1
    assert out["extra_parent_ids"] == [f"{TM}:vi:table_4_3"]
    assert deps.retriever.calls[-1]["queries"] == ["bán kính uốn cong cáp"]
    assert any(h["section_key"] == "table_5_7_1" for h in out["reranked"])
