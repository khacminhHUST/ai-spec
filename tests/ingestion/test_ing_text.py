"""Unit: tách câu (1), heading regex (2), referenced_ids (7)."""
from __future__ import annotations

import pytest

from app.services.ingestion.parse import Block
from app.services.ingestion.sentences import split_sentences
from app.services.ingestion.structure import build_structure, find_candidates
from app.services.ingestion.textutil import profile
from app.utils.keys import extract_references

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 1. tách câu

@pytest.mark.parametrize("text", [
    "Tiết diện tối thiểu 1.5mm² cho chiếu sáng.",
    "Khoảng cách 3,5m tính từ trần.",
    "Xem mục 5.7.1 về cáp chống cháy.",
    "Theo TCVN 5687:2024 về thông gió.",
    "Theo QCVN 06:2022/BXD về an toàn cháy.",
    "Khay, thang, hộp cáp v.v. Bản này áp dụng.",
    "Phát hành Rev.01 Đã duyệt.",
    "Cable No. 5 Shall be used.",
    "Khu vực khối đế gaming., chiều dài của tuyến cáp.",   # không cắt khi sau dấu chấm không có khoảng trắng
])
def test_khong_cat_trong_mau_bi_che(text):
    sents = split_sentences(text, "vi")
    assert "".join(sents) == text
    for frag in ("1.5mm²", "3,5m", "5.7.1", "TCVN 5687:2024", "QCVN 06:2022/BXD", "v.v.", "Rev.01", "No. 5"):
        if frag in text:
            assert any(frag in s for s in sents), (frag, sents)


def test_cat_dung_o_dau_cau_va_bullet():
    t = "Cáp phải được bảo vệ. Không được đặt cáp khi chưa có ống! Có đúng không? Có; Không.\n• Mục một.\na) Đo cách điện."
    sents = split_sentences(t, "vi")
    assert "".join(sents) == t
    assert [s.strip() for s in sents] == [
        "Cáp phải được bảo vệ.", "Không được đặt cáp khi chưa có ống!", "Có đúng không?", "Có;", "Không.",
        "• Mục một.", "a) Đo cách điện."]


def test_tach_cau_tieng_anh():
    t = "Cables shall comply with IEC 60502-1. The radius is 2.5 m. Use e.g. LSZH cables. See Section 15C."
    sents = split_sentences(t, "en")
    assert [s.strip() for s in sents] == ["Cables shall comply with IEC 60502-1.", "The radius is 2.5 m.",
                                          "Use e.g. LSZH cables.", "See Section 15C."]


# ---------------------------------------------------------------- 2. heading regex

def _cand(doc_type: str, text: str):
    b = Block("heading", text, 5, (60, 100, 300, 110), label="section_header")
    c = find_candidates([b], profile(doc_type))
    return (c[0].level["kind"], c[0].num) if c else None


@pytest.mark.parametrize("doc_type,text,expected", [
    ("SPEC", "5.7 WIRES AND CABLES", ("sec", "5.7")),
    ("SPEC", "5.7.1 FIRE RESISTANT CABLES (FR cable)", ("sec", "5.7.1")),
    ("TECH_INSTRUCTION", "CHƯƠNG 2 – HỆ THỐNG CHỮA CHÁY TỰ ĐỘNG", ("chap", "2")),
    ("TECH_INSTRUCTION", "4.1. Tổng quan", ("sec", "4.1")),
    ("BRAND_STANDARD", "4C.6 Pool Equipment", ("sec", "4C.6")),
    ("BRAND_STANDARD", "9.7 HVAC - Mechanical, Electrical & Plumbing (MEP) Spaces", ("sec", "9.7")),
    ("DESIGN_REPORT", "I. HỆ THỐNG CƠ KHÍ:", ("part", "I")),
    ("DESIGN_REPORT", "4.2 Điều kiện thiết kế", ("sec", "4.2")),
    ("DESIGN_REPORT", "e. Hệ thống thông gió:", ("sec", "e")),
    ("CONTRACT", "Điều 5. Giá hợp đồng", ("art", "5")),
    ("LEGAL_STANDARD", "Phụ lục A (quy định)", ("appx", "A")),
])
def test_heading_regex(doc_type, text, expected):
    assert _cand(doc_type, text) == expected


@pytest.mark.parametrize("doc_type,text", [
    ("SPEC", "1.5mm2"),                      # số đơn vị, không phải mục
    ("DESIGN_REPORT", "8 Ống uPVC theo BS 3505"),   # số 1 cấp thiếu dấu chấm
    ("BRAND_STANDARD", "Overview & Project Administration"),
])
def test_heading_regex_khong_nhan_nham(doc_type, text):
    assert _cand(doc_type, text) is None


def _blocks(texts):
    return [Block("heading" if t.startswith("#") else "text", t.lstrip("# "), 1 + i // 20, (60, 10 + i, 300, 20 + i),
                  label="section_header" if t.startswith("#") else "text") for i, t in enumerate(texts)]


def test_cay_muc_va_section_key_thuyet_minh_scope():
    st = build_structure(_blocks(["# I. CƠ KHÍ", "# 1. TỔNG QUAN", "x", "# 4. ĐHKK", "# 4.2 Điều kiện", "# e. Thông gió",
                                  "y", "# f. Gió thải", "# II. ĐIỆN", "# 1. TỔNG QUAN", "# 7. NỐI ĐẤT",
                                  "# 1. Hệ thống nối đất dùng cọc"]), profile("DESIGN_REPORT"))
    keys = [s.key for s in st.sections]
    assert keys == ["sec_0", "part_i", "part_i__sec_1", "part_i__sec_4", "part_i__sec_4_2", "part_i__sec_4_2_e",
                    "part_i__sec_4_2_f", "part_ii", "part_ii__sec_1", "part_ii__sec_7"]
    assert st.scoped
    sec = {s.key: s for s in st.sections}
    assert sec["part_i__sec_4_2_e"].parent_key == "part_i__sec_4_2"
    assert len(st.rejected) == 1  # "1." sau "7." → không phải heading


def test_chuong_ptn_scope_always():
    st = build_structure(_blocks(["# CHƯƠNG 1 – TỔNG QUAN", "# 1. TỔNG QUAN", "# 2. ĐIỀU KIỆN KHÍ HẬU", "z",
                                  "# CHƯƠNG 2 – HỆ THỐNG", "# 1. TỔNG QUAN", "# 4.1. Tổng quan"]),
                         profile("TECH_INSTRUCTION"))
    assert [s.key for s in st.sections][1:] == ["chap_1", "chap_1__sec_1", "chap_1__sec_2", "chap_2", "chap_2__sec_1",
                                               "chap_2__sec_4_1"]


def test_westin_chuong_tu_muc_luc():
    blocks = [Block("text", "cover", 1, (0, 0, 1, 1)), Block("heading", "9.7 HVAC - MEP Spaces", 3, (0, 0, 1, 1),
                                                             label="list_item"),
              Block("text", "Program: ...", 3, (0, 2, 1, 3)), Block("heading", "10.7 Electrical", 5, (0, 0, 1, 1)),
              Block("heading", "9.8 Wrong chapter", 5, (0, 2, 1, 3))]
    chapters = [{"num": "9", "title": "Engineering", "page": 2}, {"num": "10", "title": "F&B BOH", "page": 4}]
    st = build_structure(blocks, profile("BRAND_STANDARD"), chapters)
    assert [s.key for s in st.sections] == ["sec_0", "sec_9", "sec_9_7", "sec_10", "sec_10_7"]
    assert st.rejected  # 9.8 nằm trong chương 10 → loại


# ---------------------------------------------------------------- 7. referenced_ids

def test_referenced_ids_chuan_hoa():
    refs = extract_references("Theo TCVN 5687: 2024 và Bảng 4.3; tham khảo ANSI/ASHRAE Standard 62.1-2022, "
                              "ASHRAE 62.1, QCVN 06: 2022/BXD, xem Section 15C và Mục 5.7.")
    for r in ("TCVN 5687:2024", "table_4_3", "ASHRAE 62.1", "sec_15c", "QCVN 06:2022/BXD", "sec_5_7"):
        assert r in refs, (r, refs)
