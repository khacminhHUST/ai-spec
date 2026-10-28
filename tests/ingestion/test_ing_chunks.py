"""Unit: bảng (4), output LLM chunking (5), toàn vẹn (6), metadata (8)."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.models.document import ParentChunk
from app.providers.llm import MockChatModel
from app.services.ingestion import chunk_llm
from app.services.ingestion.bilingual import attach_captions, merge_page_break_tables
from app.services.ingestion.chunks import (
    ChildDraft,
    ParentDraft,
    Unit,
    build_children,
    integrity_check,
    is_group_row,
    table_children,
)
from app.services.ingestion.metadata import DocMeta, infer_metadata, issue_date_from, revision_from
from app.services.ingestion.parse import Block
from app.services.ingestion.textutil import profile
from app.utils.keys import make_parent_id

pytestmark = pytest.mark.unit

META = DocMeta("doc_test0000001", "0" * 64, "x.pdf", project_id="26DA06", project_name="Quảng Trường Biển 88ha",
               building="S5", stream="DESIGN_CONSULTANT", doc_type="DESIGN_REPORT", disciplines=["HVAC"])


# ---------------------------------------------------------------- 4. bảng

def _tbl(page, rows, header_rows=0, bbox=(80, 100, 500, 700)):
    return Block("table", "", page, bbox, label="table", rows=rows, header_rows=header_rows)


def test_ghep_bang_ngat_trang_bo_header_lap():
    hdr = ["Function", "Rate"]
    blocks = [Block("text", "intro", 10, (0, 0, 1, 1)),
              _tbl(10, [hdr, ["Main Lobby", "3.8"], ["Hotel Room", "Hotel Room"]], header_rows=1),
              _tbl(11, [hdr, ["Guest", "2.5"], ["Corridor", "Min 2 ACH"]]),
              Block("caption", "Bảng 4.3", 11, (0, 800, 1, 810))]
    out = merge_page_break_tables(blocks, [])
    tables = [b for b in out if b.kind == "table"]
    assert len(tables) == 1
    t = tables[0]
    assert t.rows == [hdr, ["Main Lobby", "3.8"], ["Hotel Room", "Hotel Room"], ["Guest", "2.5"],
                      ["Corridor", "Min 2 ACH"]]
    assert (t.page, t.page_end) == (10, 11)
    assert t.meta["row_pages"] == [10, 10, 10, 11, 11]
    attach_captions(out, profile("DESIGN_REPORT"))
    assert t.number == "4.3"


def test_khong_ghep_khi_khac_so_cot():
    out = merge_page_break_tables([_tbl(1, [["a", "b"]]), _tbl(2, [["a", "b", "c"]])], [])
    assert len(out) == 2


def _table_draft(rows, header, caption="Bảng 4.3"):
    p = ParentChunk(parent_id=make_parent_id(META.doc_id, "vi", "table_4_3"), doc_id=META.doc_id,
                    section_key="table_4_3", lang="vi", type="TABLE", content_md="x")
    from app.services.ingestion.chunks import data_row

    return ParentDraft(p, "e. Thông gió > Bảng 4.3", "\n".join(data_row(r) for r in rows),
                       table={"caption": caption, "name": caption, "header": [header], "rows": rows,
                              "row_pages": [10] * len(rows), "ncol": len(header)})


def test_child_bang_theo_nhom_luon_co_ten_bang_header_nhom():
    header = ["Function", "Densities (m²/person)", "Outdoor Air Flow Rate (L/s.person)", "Recommendation"]
    rows = []
    for g in ("General", "Food and Beverage Service", "Hotel Room", "Sports & Entertainment"):
        rows.append([g] * 4)
        for k in range(4):
            rows.append([f"{g} space {k} with a long descriptive name", "10", "2.5 L/s.person 0.3 L/s/m2",
                         "Min 2 ACH per TCVN 5687:2024"])
    d = _table_draft(rows, header)
    warns: list[str] = []
    kids = table_children(d, "vi", max_tokens=200, bc_tokens=40, warnings=warns)
    assert len(kids) >= 4
    groups = [k.topic for k in kids]
    assert {"General", "Hotel Room"} <= set(groups)
    for k in kids:
        assert k.prefix.startswith("Bảng 4.3") and "| Function |" in k.prefix
        # nhóm có trong raw (dòng nhóm) hoặc trong prefix (child tiếp theo của cùng nhóm)
        assert k.topic in k.raw or f"[{k.topic}]" in k.prefix
    hotel = [k for k in kids if k.topic == "Hotel Room"]
    assert "Hotel Room space 0" in hotel[0].raw
    assert all("Hotel Room" not in k.raw for k in kids if k.topic != "Hotel Room")
    assert "".join(k.raw for k in kids).replace("\n", "") == d.body.replace("\n", "")


def test_bang_nho_1_child():
    rows = [["Toilets-private", "25 L/s/unit"], ["Toilets-public", "25 L/s/room"]]
    kids = table_children(_table_draft(rows, ["Phòng", "Định mức gió thải"]), "vi", 400, 40, [])
    assert len(kids) == 1 and "Toilets-public" in kids[0].raw


def test_dong_phan_nhom():
    assert is_group_row(["Hotel Room"] * 7)
    assert is_group_row(["General", "", ""])
    assert not is_group_row(["Guest", "10", "9.7"])
    assert not is_group_row(["1.5", "1.5"])


# ---------------------------------------------------------------- 5. output LLM chunking

UNIT_TOKENS = [50, 60, 70, 80, 90]


def test_llm_nhom_hop_le_duoc_chap_nhan():
    raw = [{"start": 1, "end": 2, "topic": "A"}, {"start": 3, "end": 5, "topic": "B"}]
    groups, err = chunk_llm.validate_groups(raw, 5, UNIT_TOKENS, 400)
    assert err is None and groups == [(0, 1, "A"), (2, 4, "B")]


@pytest.mark.parametrize("raw,why", [
    ([{"start": 1, "end": 3}, {"start": 3, "end": 5}], "chồng"),
    ([{"start": 1, "end": 2}, {"start": 4, "end": 5}], "thiếu"),
    ([{"start": 1, "end": 2}, {"start": 3, "end": 4}], "không phủ hết"),
    ([{"start": 1, "end": 5}], "vượt token"),
    ("not a list", "sai định dạng"),
])
def test_llm_nhom_sai_bi_tu_choi(raw, why):
    groups, err = chunk_llm.validate_groups(raw, 5, UNIT_TOKENS, 250)
    assert groups is None and err, why


def test_llm_sai_thi_fallback_gop_tham_lam(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_llm, "CACHE_DIR", tmp_path)
    units = [f"Câu số {i}." for i in range(5)]
    bad = MockChatModel(handler=lambda m: json.dumps([{"start": 1, "end": 3}, {"start": 3, "end": 5}]))
    g = chunk_llm.group_units(units, UNIT_TOKENS, [True] * 5, bad, 200, 80)
    assert g.source == "fallback" and "chồng" in g.reason
    assert g.groups == [(0, 2, None), (3, 4, None)]  # 50+60+70=180 ≤ 200; 80+90
    good = MockChatModel(handler=lambda m: '```json\n[{"start":1,"end":2,"topic":"a"},{"start":3,"end":5,"topic":"b"}]\n```')
    g2 = chunk_llm.group_units(units, [50, 60, 40, 40, 40], [True] * 5, good, 200, 80)
    assert g2.source == "llm" and g2.groups == [(0, 1, "a"), (2, 4, "b")]
    # lần 2 lấy từ cache, không gọi LLM
    n = len(good.calls)
    g3 = chunk_llm.group_units(units, [50, 60, 40, 40, 40], [True] * 5, good, 200, 80)
    assert g3.source == "llm_cache" and len(good.calls) == n


def test_greedy_gop_nhom_cuoi_qua_nho():
    assert chunk_llm.greedy_groups([150, 150, 30], 320, 80) == [(0, 0, None), (1, 2, None)]
    assert chunk_llm.greedy_groups([100, 100, 100], 400, 80) == [(0, 2, None)]
    assert chunk_llm.greedy_groups([300, 300, 30], 400, 80) == [(0, 0, None), (1, 2, None)]
    assert chunk_llm.greedy_groups([390, 30], 400, 80) == [(0, 0, None), (1, 1, None)]  # không thể cân bằng


# ---------------------------------------------------------------- 6. toàn vẹn

def _section_draft(paras: list[str]) -> ParentDraft:
    p = ParentChunk(parent_id=make_parent_id(META.doc_id, "vi", "sec_5_7"), doc_id=META.doc_id, section_key="sec_5_7",
                    lang="vi", type="SECTION", content_md="x")
    return ParentDraft(p, "5.7 DÂY VÀ CÁP ĐIỆN", "\n\n".join(paras), [Unit(t, 21, True) for t in paras])


def test_toan_ven_noi_child_bang_parent_va_mat_cau_thi_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_llm, "CACHE_DIR", tmp_path)
    paras = [("Cáp phải được bảo vệ trong khay cáp, thang cáp hoặc ống luồn dây theo TCVN 9208:2012. " * 6).strip()
             for _ in range(8)]
    d = _section_draft(paras)
    kids, stats = build_children([d], META, "vi", MockChatModel(responses=["xx"]), workers=1, use_cache=False)
    assert len(kids) > 1 and stats["fallback"] == 1
    assert integrity_check(d, kids)
    # cố ý làm mất 1 câu → phải fail
    k = kids[1]
    broken = k.model_copy(update={"raw_text": k.raw_text.split(". ", 1)[1]})
    assert not integrity_check(d, [kids[0], broken, *kids[2:]])
    for c in kids:
        assert c.text.startswith("[Quảng Trường Biển 88ha S5 | DESIGN_CONSULTANT | HVAC | 5.7 DÂY VÀ CÁP ĐIỆN")
        assert c.page_start == 21


# ---------------------------------------------------------------- 8. metadata

def test_metadata_tu_duong_dan_va_ten_file(tmp_path):
    root = "D:/ai-spec/S5_Bo-tai-lieu-mau_GD-thiet-ke_20261020"
    cover = [Block("heading", "ELECTRICAL SERVICES SPECIFICATION ELECTRICAL INSTALLATION", 1, (0, 0, 1, 1)),
             Block("text", "DỰ ÁN: KHU ĐÔ THỊ DỊCH VỤ QUẢNG TRƯỜNG BIỂN (88HA) CÔNG TRÌNH: TÒA NHÀ S5", 1, (0, 0, 1, 1)),
             Block("table", "", 1, (0, 0, 1, 1), rows=[["No. STT", "Date Ngày phát hành", "Revision Hiệu chỉnh"],
                                                     ["1", "10-2026", "00"]])]
    m = infer_metadata(f"{root}/2_Yeu cau ky thuat (Spec)/BDD_S5_E5_ELECTRICAL ISTALLATION SPEC_16102026_FINAL.pdf",
                       "a" * 64, cover)
    assert (m.project_id, m.building, m.stream, m.doc_type, m.disciplines) == (
        "26DA06", "S5", "DESIGN_CONSULTANT", "SPEC", ["ELECTRICAL"])
    assert m.revision == "00" and m.issue_date == date(2026, 10, 16)
    assert m.doc_code == "26DA06_S5_DESIGN_CONSULTANT_ELECTRICAL_SPEC" and m.doc_id == "doc_" + "a" * 12
    assert m.issuer is None and any("issuer" in w for w in m.warnings)


def test_metadata_thuyet_minh_va_westin():
    root = "D:/ai-spec/S5_Bo-tai-lieu-mau_GD-thiet-ke_20261020"
    tm = infer_metadata(f"{root}/1_Thuyet minh ky thuat/20261017-Bai Dat Do-S5-Detail Design Report-VN.pdf", "b" * 64,
                        [Block("text", "Đơn vị tư vấn Thiết kế CÔNG TY TNHH CPG VIỆT NAM", 2, (0, 0, 1, 1)),
                         Block("table", "", 1, (0, 0, 1, 1), rows=[["STT", "Ngày phát hành", "Hiệu chỉnh"],
                                                                 ["1", "17/10/2026", "Rev.01"]])])
    assert (tm.stream, tm.disciplines[0], tm.revision, tm.issue_date, tm.issuer, tm.stage) == (
        "DESIGN_CONSULTANT", "HVAC", "01", date(2026, 10, 17), "CPG", "TKKT")
    w = infer_metadata(f"{root}/4_Tieu chuan Marriott/Westin Design Standards-Managed_202512.pdf", "c" * 64, [])
    assert (w.stream, w.disciplines, w.revision) == ("OPERATOR_STANDARD", ["GENERAL"], None)
    assert w.issue_date == date(2025, 12, 1) and any("chỉ có tháng" in x for x in w.warnings)
    assert any("revision" in x for x in w.warnings)


def test_metadata_khong_suy_duoc_thi_null_va_warning(tmp_path):
    m = infer_metadata(tmp_path / "abc" / "tai_lieu_khong_ro.pdf", "d" * 64, [])
    assert m.project_id is None and m.stream is None and m.revision is None and m.issue_date is None
    assert m.doc_code is None
    joined = " ".join(m.warnings)
    for f in ("project_id", "building", "folder_map", "revision", "issue_date"):
        assert f in joined


def test_revision_va_ngay_tu_ten_file():
    assert revision_from("Spec_Rev.02.pdf", "", [])[0] == "02"
    assert revision_from("Spec_R03.pdf", "", [])[0] == "03"
    assert issue_date_from("20261017-x.pdf", "")[0] == date(2026, 10, 17)
    assert issue_date_from("x_16102026_FINAL.pdf", "")[0] == date(2026, 10, 16)
    assert issue_date_from("x.pdf", "Hà Nội, 2026")[0] is None
