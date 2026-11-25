"""Unit – CLI upload hàng loạt (app/cli/bulk_upload.py): gợi ý phân hệ/tiêu đề, khớp dự án, kế hoạch thư mục,
gọi API (httpx.MockTransport – không gọi mạng), ghi kết quả và chạy lại bỏ qua file đã xong."""
from __future__ import annotations

import csv
import json

import httpx
import pytest

from app.cli import bulk_upload as bu

pytestmark = pytest.mark.unit

Z = "Z:/ME-DA-01\\2026\\26DA08-DREAM CITY\\01. BIM\\250703 MEP R0 CT2\\4. SPECIFICATION"


@pytest.mark.parametrize("src, want", [
    (Z + "\\04. FIRE PROTECTION\\SECTION 4- PUMPS.pdf", ["FIRE"]),
    (Z + "\\04. FIRE PROTECTION\\SECTION 11 - ELECTRICAL INSTALLATION.pdf", ["FIRE"]),   # thư mục gần nhất thắng
    (Z + "\\05. ACMV-RESIDENTIAL\\Section 5 - FANS.pdf", ["HVAC"]),
    (Z + "\\06. SWIMNING POOL\\Section 5 ELECTRICAL INSTALLATION.pdf", ["PLUMBING"]),
    (Z + "\\02. LIFT\\SECTION 1 - GENERAL REQUIREMENTS.pdf", ["LIFT"]),
    ("Z:/x/03_ Spec.zip → trong zip: 03. Spec/05. ELV/5.5 CCTV.pdf", ["ELV"]),
    ("Z:/x/BP09/psf/ff/FF-Chapter-01-General requirments.pdf", ["FIRE"]),
    ("Z:/x/BP09/psf/ps/Ps-chapter-09-Sound.pdf", ["PLUMBING"]),
    ("Z:/x/03. Spec/04. EL/4.15 Generator.pdf", ["ELECTRICAL"]),
    ("Z:/x/DRC607_CT05_DWG_TDR_MEP_15.04.26/04. SPECIFICATION/DRC607(CT5)- DD - Spec.pdf", []),  # nhiều bộ môn → LLM
    ("Z:/x/MEP & FP Brief/HVAC and Fire/a.pdf", []),          # thư mục ghép 2 bộ môn → không đoán
    ("", []),
])
def test_discipline_hint(src, want):
    assert bu.discipline_hint(src) == want


def test_title_hint_chi_cho_ten_chung_chung():
    src = Z + "\\04. FIRE PROTECTION\\SECTION 4- PUMPS.pdf"
    assert bu.title_hint("SECTION 4- PUMPS (2).pdf", src) == "FIRE PROTECTION – SECTION 4- PUMPS"
    assert bu.title_hint("CT5_Tender Design_Report.pdf", src) is None
    assert bu.title_hint("SECTION 4- PUMPS.pdf", "") is None


def test_match_project_theo_ma_dau_thu_muc():
    projects = [{"project_id": "26DA08", "aliases": []}, {"project_id": "X1", "aliases": ["0125DA003"]}]
    assert bu.project_code("2025_0125DA003-The9Stellar") == "0125DA003"
    assert bu.match_project("26DA08-DREAM CITY", projects) == "26DA08"
    assert bu.match_project("2025_0125DA003-The9Stellar", projects) == "X1"
    assert bu.match_project("26DA18-DREAM CT05", projects) is None


def _tree(tmp_path):
    root = tmp_path / "up"
    for rel in ["26DA08-DREAM CITY/DESIGN_CONSULTANT/SECTION 4- PUMPS.pdf",
                "26DA08-DREAM CITY/DESIGN_CONSULTANT/CT-02_CT_MEP_Report.pdf",
                "26DA08-DREAM CITY/KHAC/bo_qua.pdf",
                "26DA18-DREAM CT05/DESIGN_CONSULTANT/a.pdf",
                "_NGOAI_MEP_xem_truoc/26DA08-DREAM CITY/DESIGN_CONSULTANT/arch.pdf"]:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"%PDF-1.7 test")
    with (root / bu.MANIFEST).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Trạng thái", "File đã chép", "Nguồn trên ổ Z"])
        w.writerow(["ĐÃ CHÉP", "26DA08-DREAM CITY\\DESIGN_CONSULTANT\\SECTION 4- PUMPS.pdf",
                    Z + "\\04. FIRE PROTECTION\\SECTION 4- PUMPS.pdf"])
    return root


def test_plan_bo_qua_thu_muc_gach_duoi_va_luong_la(tmp_path):
    root = _tree(tmp_path)
    got = [(p, s, f.name) for p, s, f in bu.plan(root)]
    assert ("26DA08-DREAM CITY", "DESIGN_CONSULTANT", "SECTION 4- PUMPS.pdf") in got
    assert not any(n in ("bo_qua.pdf", "arch.pdf") for *_, n in got)
    assert any(f.name == "arch.pdf" for *_, f in bu.plan(root, include_underscore=True))


def test_main_upload_ghi_ket_qua_va_chay_lai_bo_qua(tmp_path, capsys):
    root = _tree(tmp_path)
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/projects":
            return httpx.Response(200, json=[{"project_id": "26DA08", "aliases": []}])
        assert req.url.path == "/knowledge/uploads"
        assert req.headers["authorization"] == "Bearer tok"
        body = req.read().decode("utf-8", "replace")
        meta = json.loads(body.split('name="metadata"')[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
        sent.append(meta)
        if "CT-02_CT_MEP_Report" in body:
            return httpx.Response(409, json={"detail": "file đã có trong kho"})
        return httpx.Response(201, json={"job_id": "job_1"})

    real = httpx.Client
    bu.httpx.Client = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        assert bu.main([str(root), "--api", "http://t", "--token", "tok"]) == 0
        pumps = next(m for m in sent if m.get("title"))
        assert pumps["project_id"] == "26DA08" and pumps["stream"] == "DESIGN_CONSULTANT"
        assert pumps["disciplines"] == ["FIRE"] and pumps["title"] == "FIRE PROTECTION – SECTION 4- PUMPS"
        assert "disciplines" not in next(m for m in sent if not m.get("title"))   # không rõ → worker tự nhận diện
        out = capsys.readouterr().out
        assert "26DA18-DREAM CT05 → 26DA18" in out                              # dự án chưa có → báo lại
        rows = list(csv.DictReader((root / bu.RESULT).open(encoding="utf-8-sig")))
        assert sorted(r["kết quả"] for r in rows) == ["OK", "ĐÃ CÓ"]
        sent.clear()
        assert bu.main([str(root), "--api", "http://t", "--token", "tok"]) == 0
        assert sent == []                                                        # chạy lại: bỏ qua file đã xong
    finally:
        bu.httpx.Client = real


def test_token_het_han_thi_dung(tmp_path):
    root = _tree(tmp_path)

    def handler(req):
        if req.url.path == "/projects":
            return httpx.Response(200, json=[{"project_id": "26DA08", "aliases": []}])
        return httpx.Response(401, json={"detail": "hết hạn"})

    real = httpx.Client
    bu.httpx.Client = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        with pytest.raises(SystemExit, match="token"):
            bu.main([str(root), "--api", "http://t", "--token", "tok"])
    finally:
        bu.httpx.Client = real
