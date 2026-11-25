"""Unit – tiêu đề tài liệu từ trang bìa (metadata.title_from): bỏ quốc hiệu / tiêu ngữ / dòng "Hạng mục:"."""
from __future__ import annotations

import pytest

from app.services.ingestion.metadata import is_title_noise, title_from
from app.services.ingestion.parse import Block

pytestmark = pytest.mark.unit


def _b(text: str, kind: str = "heading", page: int = 1) -> Block:
    return Block(kind=kind, text=text, page=page, bbox=(0, 0, 0, 0))


@pytest.mark.parametrize("text", [
    "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",
    "CONG HOA XA HOI CHU NGHIA VIET NAM",          # OCR mất dấu
    "Độc lập - Tự do - Hạnh phúc",
    "Độc lập — Tự do — Hạnh phúc",
    "SOCIALIST REPUBLIC OF VIETNAM",
    "HẠNG MỤC: CƠ ĐIỆN",
    "Dự án: Khu đô thị Dream City",
])
def test_dong_nhieu_tren_bia(text):
    assert is_title_noise(text)


@pytest.mark.parametrize("text", [
    "THUYẾT MINH HỒ SƠ THIẾT KẾ THI CÔNG HẠNG MỤC: CƠ ĐIỆN",
    "SECTION 2.10: TANKS AND VESSELS",
    "HỢP ĐỒNG THI CÔNG",
])
def test_tieu_de_that_giu_nguyen(text):
    assert not is_title_noise(text)


def test_title_bo_quoc_hieu_lay_dong_tieu_de_that():
    blocks = [_b("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"), _b("Độc lập - Tự do - Hạnh phúc", "text"),
              _b("HẠNG MỤC: CƠ ĐIỆN"), _b("THUYẾT MINH TKKT PHẦN CƠ ĐIỆN"), _b("Chương 1 Tổng quan", page=2)]
    assert title_from(blocks, "x.pdf") == "THUYẾT MINH TKKT PHẦN CƠ ĐIỆN"


def test_title_chi_co_quoc_hieu_thi_lay_ten_file():
    blocks = [_b("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"), _b("Độc lập - Tự do - Hạnh phúc", "text")]
    assert title_from(blocks, "CT5_TKTC-Report.pdf") == "CT5 TKTC Report"
