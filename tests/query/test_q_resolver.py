"""Unit N2 – Project Resolver."""
import pytest

from query.resolver import apply_action, build_profile, clarify_message, match_mention, resolve
from tests.query.fakes import PQ, S5, S6, S7, make_deps

pytestmark = pytest.mark.unit


@pytest.fixture
def deps(mock_settings):
    return make_deps(mock_settings)


@pytest.mark.parametrize("mention", ["Bãi Đất Đỏ", "bai dat do", "BDD", "bãi đất đỏ", "BAI-DAT-DO", "S5"])
def test_alias_normalization(deps, mention):
    rows = deps.repo.aliases(deps.repo.user_projects("test_engineer"))
    assert match_mention(mention, rows, 85) == [S5]


def test_fuzzy_match(deps):
    rows = deps.repo.aliases([S5])
    assert match_mention("Bai Dat Doo", rows, 85) == [S5]
    assert match_mention("Quang Truong Bien 88", rows, 85) == [S5]
    assert match_mention("Hoàn toàn khác", rows, 85) == []


def test_one_mention_many_projects_asks(deps):
    res = resolve(deps, "multi", "set", ["Phú Quốc"], [])
    assert res.needs_clarify and set(res.ambiguous["Phú Quốc"]) == {S5, PQ}
    msg, options = clarify_message(deps, "multi", res)
    assert {o["project_id"] for o in options} == {S5, PQ} and "nhiều dự án" in msg


def test_project_without_permission_is_dropped(deps):
    res = resolve(deps, "test_engineer", "set", ["S6"], [S5])
    assert res.unmatched == ["S6"] and res.needs_clarify
    assert S6 not in res.active_projects


def test_unmatched_mention_asks(deps):
    res = resolve(deps, "multi", "set", ["Dự án Mars"], [S5])
    assert res.needs_clarify and res.unmatched == ["Dự án Mars"] and res.active_projects == [S5]


def test_empty_active_asks_when_many_projects(deps):
    res = resolve(deps, "multi", "keep", [], [])
    assert res.needs_clarify and not res.active_projects
    msg, options = clarify_message(deps, "multi", res)
    assert len(options) == 4 and "dự án" in msg


def test_empty_active_single_project_auto_selects(deps):
    res = resolve(deps, "test_engineer", "keep", [], [])
    assert not res.needs_clarify and res.active_projects == [S5] and res.auto_selected


def test_state_transitions(deps):
    """Bảng chuyển trạng thái trong spec mục 6."""
    turns = [("set", ["S5", "S6"], [S5, S6]), ("keep", [], [S5, S6]), ("set", ["S5"], [S5]),
             ("add", ["S7"], [S5, S7]), ("remove", ["S7"], [S5])]
    active: list[str] = []
    for action, mentions, expected in turns:
        res = resolve(deps, "multi", action, mentions, active)
        assert not res.needs_clarify
        active = res.active_projects
        assert active == expected, (action, mentions)


def test_apply_action_keep_with_mention_when_empty():
    assert apply_action("keep", [], ["X"]) == ["X"]
    assert apply_action("set", ["A"], []) == ["A"]


def test_profile_languages_by_stream(deps):
    prof = build_profile(deps, S5)
    dc = prof["streams"]["DESIGN_CONSULTANT"]
    assert set(dc["languages"]) == {"vi", "en"} and len(dc["doc_ids"]) == 3
    assert prof["source_priority"][0] == "CONTRACT" and prof["governing_language"] == "vi"


def test_profile_cache_ttl(deps):
    from query.resolver import load_profiles

    load_profiles(deps, [S5])
    n = deps.repo.calls.count("documents")
    load_profiles(deps, [S5])
    assert deps.repo.calls.count("documents") == n  # lấy từ cache
