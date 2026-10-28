"""Unit N5 – Context Builder."""
import pytest

from app.services.query.context import build_context
from app.services.query.resolver import build_profile
from tests.query.fakes import S5, make_deps

pytestmark = pytest.mark.unit
SPEC = "doc_bd42fa85440e"
TM = "doc_11833346273d"


@pytest.fixture
def deps(mock_settings):
    return make_deps(mock_settings)


def _hits(deps, keys, score=0.9):
    """keys: list (parent_id, seq) → hit giả từ seed."""
    out = []
    for pid, seq in keys:
        c = next(c for c in deps.retriever.data.children if c.child_id == f"{pid}#{seq:04d}")
        out.append({**c.model_dump(), "rerank_score": score})
        score -= 0.01
    return out


def _prof(deps):
    return {S5: build_profile(deps, S5)}


def test_merge_vi_en_same_section(deps):
    ctx = build_context(deps, _hits(deps, [(f"{SPEC}:vi:sec_5_23", 1)]), _prof(deps))
    blk = next(b for b in ctx if b["section_key"] == "sec_5_23")
    assert "24 tháng" in blk["vi"] and "24 months" in blk["en"]
    assert blk["page_start"] == 48 and blk["page_end"] == 49 and blk["revision"] == "00"
    assert blk["stream"] == "DESIGN_CONSULTANT" and blk["priority"] == 2


def test_dedupe_parents_and_single_query(deps):
    hits = _hits(deps, [(f"{SPEC}:vi:sec_5_7", 1), (f"{SPEC}:vi:sec_5_7", 3), (f"{SPEC}:en:sec_5_7", 2)])
    deps.repo.calls.clear()
    ctx = build_context(deps, hits, _prof(deps))
    assert [b["section_key"] for b in ctx].count("sec_5_7") == 1
    assert deps.repo.calls.count("parents_by_sections") == 1


def test_reference_one_hop_and_unresolved(deps):
    ctx = build_context(deps, _hits(deps, [(f"{TM}:vi:part_i__sec_4_2", 1)]), _prof(deps))
    keys = [(b["section_key"], b["is_reference"]) for b in ctx]
    assert keys[0] == ("part_i__sec_4_2", False)
    assert ("table_4_3", True) in keys                      # Bảng 4.3 lấy qua referenced_ids (1 hop)
    main = ctx[0]
    assert "ASHRAE 62.1-2013" in main["unresolved_refs"]   # tiêu chuẩn ngoài hệ thống → nhãn
    assert "TCVN 5687:2024" in main["unresolved_refs"]


def test_long_parent_trimmed_to_neighbors(mock_settings):
    deps = make_deps(mock_settings, pipeline_overrides={"context": {"max_parent_tokens": 50, "neighbor_children": 1}})
    ctx = build_context(deps, _hits(deps, [(f"{SPEC}:vi:sec_5_7", 1)]), {S5: build_profile(deps, S5)})
    blk = next(b for b in ctx if b["section_key"] == "sec_5_7")
    assert blk["vi"].startswith("5.7 DÂY VÀ CÁP ĐIỆN")
    assert "Tuyến cáp phải được lựa chọn" in blk["vi"]        # child 2 (lân cận)
    assert "Pha A sơn màu vàng" not in blk["vi"]             # child 3 trở đi bị bỏ
    assert "Cable route must be chosen" in blk["en"]         # bản EN cắt theo cùng seq


def test_token_budget(mock_settings):
    deps = make_deps(mock_settings, pipeline_overrides={"context": {"token_budget": 300}})
    hits = _hits(deps, [(f"{SPEC}:vi:sec_5_7", 1), (f"{SPEC}:vi:table_5_7_1", 1), (f"{SPEC}:vi:sec_5_23", 1),
                        (f"{SPEC}:vi:table_5_7_2", 1)])
    ctx = build_context(deps, hits, {S5: build_profile(deps, S5)})
    assert sum(b["tokens"] for b in ctx) <= 300
    assert ctx and "sec_5_7" not in [b["section_key"] for b in ctx]   # parent quá lớn bị bỏ
    assert [b["n"] for b in ctx] == list(range(1, len(ctx) + 1))


def test_sorted_by_rerank_score(deps):
    hits = _hits(deps, [(f"{SPEC}:vi:sec_5_23", 1), (f"{SPEC}:vi:table_5_7_1", 1)])
    hits[1]["rerank_score"] = 0.99
    ctx = build_context(deps, hits, _prof(deps))
    assert [b["section_key"] for b in ctx][:2] == ["table_5_7_1", "sec_5_23"]


def test_extra_parent_ids_from_targeted_fetch(deps):
    ctx = build_context(deps, _hits(deps, [(f"{SPEC}:vi:sec_5_23", 1)]), _prof(deps),
                        extra_parent_ids=[f"{SPEC}:vi:table_5_7_2"])
    extra = [b for b in ctx if b["section_key"] == "table_5_7_2"]
    assert extra and extra[0]["is_reference"] and extra[0]["referenced_by"] == "targeted_fetch"
