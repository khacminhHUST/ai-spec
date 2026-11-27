"""Unit: giới hạn ứng viên rerank (N4), khử khối trùng / khối chỉ có tiêu đề (N5), cắt parent dài."""
import pytest

from app.models.query import ContextBlock
from app.services.query.context import build_context, dedup_blocks, drop_heading_only, heading_only
from app.services.query.resolver import build_profile
from app.services.query.retrieval import limit_candidates, rerank
from tests.query.fakes import S5, make_deps

pytestmark = pytest.mark.unit
SPEC = "doc_bd42fa85440e"


def _c(doc, i, fusion):
    return {"child_id": f"{doc}:vi:sec_1#{i:04d}", "parent_id": f"{doc}:vi:sec_1", "doc_id": doc, "section_key": "sec_1",
            "lang": "vi", "text": f"t{doc}{i}", "fusion_score": fusion}


# ------------------------------------------------------------------ N4: giới hạn ứng viên

def test_limit_candidates_off_or_small_keeps_all():
    cands = [_c("A", i, 1 - i / 10) for i in range(5)]
    assert limit_candidates(cands, 0, 0) is cands
    assert limit_candidates(cands, 10, 2) is cands


def test_limit_candidates_top_fusion():
    cands = [_c("A", i, 0.5 - i / 100) for i in range(5)] + [_c("B", i, 0.9 - i / 100) for i in range(5)]
    got = limit_candidates(cands, 4, 0)
    assert [c["child_id"] for c in got] == [c["child_id"] for c in cands[5:9]]


def test_limit_candidates_per_doc_then_fill():
    cands = [_c("A", i, 0.9 - i / 100) for i in range(6)] + [_c("B", 0, 0.1)]
    got = limit_candidates(cands, 4, 2)
    ids = [c["child_id"] for c in got]
    # A tối đa 2 → B được chọn dù fusion thấp; còn chỗ → lấp bằng A tiếp theo theo fusion
    assert ids[:3] == [cands[0]["child_id"], cands[1]["child_id"], cands[6]["child_id"]]
    assert ids[3] == cands[2]["child_id"] and len(got) == 4


class _CountingReranker:
    def __init__(self):
        self.seen = 0

    def score(self, query, documents):
        self.seen = len(documents)
        return [0.9] * len(documents)


def test_rerank_scores_only_limited_candidates(mock_settings):
    deps = make_deps(mock_settings, pipeline_overrides={"rerank": {"max_candidates": 3, "max_candidates_per_doc": 0}})
    deps.reranker = rr = _CountingReranker()
    cands = [_c("A", i, 1 - i / 10) for i in range(8)]
    kept, _ = rerank(deps, "q", cands)
    assert rr.seen == 3 and {h["child_id"] for h in kept} == {c["child_id"] for c in cands[:3]}


# ------------------------------------------------------------------ N5: khối trùng

TEXT = ("Cable shall be installed in accordance with the manufacturer recommendations and the minimum bending radius "
        "shall not be less than six times the overall diameter of the cable for all armoured cables used on site")


def _blk(doc, sec, text, score=0.5, priority=2):
    return ContextBlock(project_id=S5, stream="DESIGN_CONSULTANT", priority=priority, doc_id=doc, section_key=sec,
                        en=text, score=score)


def test_dedup_drops_near_duplicate_from_other_doc():
    a = _blk("docA", "sec_1", TEXT, 0.9)
    b = _blk("docB", "sec_7", TEXT + " extra words here", 0.8)
    other = _blk("docC", "sec_2", "Totally different content about chilled water pipes pressure testing for two hours "
                                  "at one and a half times working pressure without any leakage observed")
    out = dedup_blocks([a, b, other], 0.8)
    assert [x.doc_id for x in out] == ["docA", "docC"]
    assert dedup_blocks([a, b, other], 0) == [a, b, other]            # tắt


def test_dedup_prefers_higher_priority_source():
    a = _blk("docA", "sec_1", TEXT, 0.9, priority=3)
    b = _blk("docB", "sec_7", TEXT, 0.8, priority=1)
    out = dedup_blocks([a, b], 0.8)
    assert len(out) == 1 and out[0].doc_id == "docB" and out[0].score == 0.9


def test_dedup_ignores_short_blocks():
    a, b = _blk("docA", "t1", "| Type | L |"), _blk("docB", "t1", "| Type | L |")
    assert len(dedup_blocks([a, b], 0.8)) == 2


def test_build_context_dedup_config(mock_settings):
    """Seed: VN + EN 5.7 cùng tài liệu → không coi là trùng (cùng doc/mục được ghép 1 khối)."""
    deps = make_deps(mock_settings, pipeline_overrides={"context": {"dedup_threshold": 0.8}})
    c = next(c for c in deps.retriever.data.children if c.child_id == f"{SPEC}:vi:sec_5_23#0001")
    ctx = build_context(deps, [{**c.model_dump(), "rerank_score": 0.9}], {S5: build_profile(deps, S5)})
    assert [b["section_key"] for b in ctx].count("sec_5_23") == 1


# ------------------------------------------------------------------ khối chỉ có tiêu đề

def _d(sec, vi=None, en=None, **kw):
    return {"doc_id": "docA", "section_key": sec, "number": "5.7", "title": "Dây và cáp điện", "vi": vi, "en": en, **kw}


def test_heading_only_detection():
    assert heading_only(_d("sec_5_7", vi="### 5.7 Dây và cáp điện\n\n5.7 Dây và cáp điện"), 60)
    assert heading_only(_d("sec_5_7", vi="## 5.7 DÂY VÀ CÁP ĐIỆN\nYêu cầu:"), 60)
    assert not heading_only(_d("sec_5_7", vi="### 5.7\n" + "Cáp phải đi trong máng, bán kính uốn ≥ 6 lần đường kính "
                                                          "ngoài của cáp theo khuyến cáo nhà sản xuất."), 60)
    assert not heading_only(_d("sec_5_7", vi="### 5.7", en="### 5.7\n" + TEXT), 60)   # 1 ngôn ngữ có nội dung
    assert not heading_only(_d("table_5_7_1", vi="| Màu | Pha |\n|---|---|\n| Vàng | A |"), 60)   # bảng


def test_drop_heading_only_keeps_blocks_with_tables_or_refs():
    title = _d("sec_5_7", vi="### 5.7 Dây và cáp điện")
    title2 = _d("sec_5_8", vi="### 5.8 Máng cáp", title="Máng cáp", number="5.8")
    tbl = _d("table_5_7_1", vi="| Màu | Pha |\n| Vàng | A |", is_reference=True, referenced_by="sec_5_7")
    full = _d("sec_5_9", vi="### 5.9\n" + "Nội dung đầy đủ " * 10, number="5.9", title="Khác")
    out = drop_heading_only([title, tbl, title2, full], 60)
    assert [b["section_key"] for b in out] == ["sec_5_7", "table_5_7_1", "sec_5_9"]   # 5.8 bị bỏ, 5.7 có bảng kèm
    unresolved = {**title2, "unresolved_refs": ["TCVN 9208:2012"]}
    assert drop_heading_only([unresolved, full], 60)[0] is unresolved                    # có tham chiếu ngoài → giữ
    assert drop_heading_only([title2], 60) == [title2]                                  # toàn tiêu đề → giữ nguyên
    assert drop_heading_only([title2, full], 0) == [title2, full]                       # tắt
