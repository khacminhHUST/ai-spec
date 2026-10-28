import pytest
from pydantic import ValidationError

from app.models.document import ChildChunk, Document, ParentChunk, child_point_id
from app.utils.keys import (
    child_seq,
    extract_references,
    is_internal_ref,
    make_child_id,
    make_parent_id,
    normalize_alias,
    section_key,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("args,expected", [
    (("5.7.1",), "sec_5_7_1"),
    (("4.3", "table"), "table_4_3"),
    (("15C.6",), "sec_15c_6"),
    (("4C.6",), "sec_4c_6"),
    (("GR1",), "sec_gr1"),
    (("9.7",), "sec_9_7"),
    (("2", "chap"), "chap_2"),
    (("I.", "part"), "part_i"),
    (("5", "art"), "art_5"),
    (("A", "appx"), "appx_a"),
])
def test_section_key(args, expected):
    assert section_key(*args) == expected


def test_section_key_scope():
    assert section_key("3", scope="chap_2") == "chap_2__sec_3"
    with pytest.raises(ValueError):
        section_key("1", "bogus")


def test_ids():
    pid = make_parent_id("doc1", "vi", "sec_5_7")
    assert pid == "doc1:vi:sec_5_7"
    cid = make_child_id(pid, 3)
    assert cid == "doc1:vi:sec_5_7#0003" and child_seq(cid) == 3
    assert child_point_id(cid) == child_point_id(cid)


@pytest.mark.parametrize("alias", ["Bãi Đất Đỏ", "bai dat do", "BAI-DAT-DO", "bãi  đất đỏ"])
def test_normalize_alias(alias):
    assert normalize_alias(alias) == "baidatdo"


def test_extract_references():
    text = ("Theo TCVN 5687:2024 và QCVN 06:2022/BXD, xem Bảng 4.3, Mục 5.7.1; "
            "refer to ASHRAE 62.1, IEC 60331-21, BS EN 50200 and Section 15C. Chương 2, Điều 5, Phụ lục A.")
    refs = extract_references(text)
    for r in ["TCVN 5687:2024", "QCVN 06:2022/BXD", "ASHRAE 62.1", "IEC 60331-21", "BS EN 50200",
              "table_4_3", "sec_5_7_1", "sec_15c", "chap_2", "art_5", "appx_a"]:
        assert r in refs, r
    assert len(refs) == len(set(refs))
    assert is_internal_ref("table_4_3") and not is_internal_ref("TCVN 5687:2024")


def test_extract_references_normalizes_spacing():
    assert extract_references("TCVN5687:2024") == ["TCVN 5687:2024"]


def _child(**kw):
    base = dict(
        child_id=make_child_id("d:vi:sec_5_7", 0), parent_id="d:vi:sec_5_7", section_key="sec_5_7", lang="vi",
        type="TEXT", text="[S5 | ...] nội dung", raw_text="nội dung", doc_id="d", stream="DESIGN_CONSULTANT",
        disciplines=["ELECTRICAL"], project_id="26DA06",
    )
    base.update(kw)
    return ChildChunk(**base)


def test_child_valid_and_payload():
    c = _child()
    p = c.qdrant_payload()
    assert p["lang"] == "vi" and p["disciplines"] == ["ELECTRICAL"] and p["is_latest"] is True


@pytest.mark.parametrize("bad", [
    {"child_id": "d:vi:sec_5_7-0"},
    {"parent_id": "d:en:sec_5_7", "child_id": "d:en:sec_5_7#0000"},
    {"raw_text": "  "},
    {"stream": "BOGUS"},
    {"unknown": 1},
])
def test_child_invalid(bad):
    with pytest.raises(ValidationError):
        _child(**bad)


def test_parent_id_validated():
    ParentChunk(parent_id="d:vi:table_4_3", doc_id="d", section_key="table_4_3", lang="vi", type="TABLE", content_md="x")
    with pytest.raises(ValidationError):
        ParentChunk(parent_id="wrong", doc_id="d", section_key="table_4_3", lang="vi", type="TABLE", content_md="x")


def test_document_nullable():
    d = Document(doc_id="d", source_path="a.pdf", file_hash="h")
    assert d.stream is None and d.is_latest and d.warnings == []
