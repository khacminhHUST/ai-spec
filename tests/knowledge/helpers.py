"""Hằng số / tiện ích dùng chung cho test kho tri thức."""
from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ingestion"
SPEC_PDF = FIXTURES / "spec_p21-22.pdf"      # trích Spec Điện S5 (mục 5.7, bảng bán kính uốn cáp)
PROJECT, BUILDING = "26DA06", "S5"
SPEC_META = {"project_id": PROJECT, "building": BUILDING, "stream": "DESIGN_CONSULTANT",
             "disciplines": ["ELECTRICAL"], "doc_type": "SPEC", "revision": "A", "issue_date": "2026-10-20",
             "series": {"mode": "new"}}
BASE_CODE = "26DA06_S5_DESIGN_CONSULTANT_ELECTRICAL_SPEC"


def variant_pdf(tmp_path: Path, tag: str) -> Path:
    """Bản sao PDF khác file_hash (thêm comment sau %%EOF – parser bỏ qua)."""
    out = tmp_path / f"spec_{tag}.pdf"
    out.write_bytes(SPEC_PDF.read_bytes() + f"\n% {tag}\n".encode())
    return out
