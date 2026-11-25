"""Unit – chặn OCR ở worker (app/services/knowledge/worker.py › prewarm_ocr): PDF scan không còn treo hàng chờ.
Không gọi Docling thật: số trang OCR và lệnh tiến trình con được thay bằng giá trị giả."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.knowledge import worker as w

pytestmark = pytest.mark.unit
JOB = {"job_id": "job_t"}
PY = sys.executable


@pytest.fixture
def steps(monkeypatch):
    got: list[str] = []
    monkeypatch.setattr(w, "_update", lambda conn, job_id, only_if=None, **f: got.append(f.get("step")) or 1)
    monkeypatch.setattr(w, "_check_cancel", lambda conn, job_id: None)
    return got


def test_khong_can_ocr_thi_bo_qua(monkeypatch, steps):
    monkeypatch.setattr(w, "ocr_pages", lambda p: (0, "0 trang scan"))
    assert w.prewarm_ocr(None, JOB, Path("x.pdf"), 50, 60) is None
    assert steps == []


def test_qua_so_trang_thi_tu_choi_ngay(monkeypatch, steps):
    monkeypatch.setattr(w, "ocr_pages", lambda p: (241, "241 trang scan"))
    with pytest.raises(w.OcrRejected, match="241 trang.*vượt giới hạn 50"):
        w.prewarm_ocr(None, JOB, Path("x.pdf"), 50, 60, cmd=[PY, "-c", "raise SystemExit(9)"])
    assert steps == []                       # không chạy OCR


def test_ocr_xong_trong_han(monkeypatch, steps):
    monkeypatch.setattr(w, "ocr_pages", lambda p: (12, "12 trang scan"))
    out = w.prewarm_ocr(None, JOB, Path("x.pdf"), 50, 60, poll_s=0.05, cmd=[PY, "-c", "print('ok')"])
    assert out["ocr_pages"] == 12 and steps == ["ocr"]


def test_qua_gio_thi_giet_tien_trinh_con(monkeypatch, steps):
    monkeypatch.setattr(w, "ocr_pages", lambda p: (12, "12 trang scan"))
    with pytest.raises(w.OcrRejected, match="OCR quá"):
        w.prewarm_ocr(None, JOB, Path("x.pdf"), 50, 0.5, poll_s=0.05,
                      cmd=[PY, "-c", "import time; time.sleep(60)"])


def test_tien_trinh_con_loi_thi_bao_stderr(monkeypatch, steps):
    monkeypatch.setattr(w, "ocr_pages", lambda p: (3, "3 trang scan"))
    with pytest.raises(RuntimeError, match="mã 2.*hong roi"):
        w.prewarm_ocr(None, JOB, Path("x.pdf"), 50, 30, poll_s=0.05,
                      cmd=[PY, "-c", "import sys; sys.stderr.write('x' * 100000 + 'hong roi'); sys.exit(2)"])


def test_job_bi_huy_thi_giet_tien_trinh_con(monkeypatch, steps):
    monkeypatch.setattr(w, "ocr_pages", lambda p: (12, "12 trang scan"))

    def cancel(conn, job_id):
        raise w.JobCancelled(job_id)

    monkeypatch.setattr(w, "_check_cancel", cancel)
    with pytest.raises(w.JobCancelled):
        w.prewarm_ocr(None, JOB, Path("x.pdf"), 50, 60, poll_s=0.05, cmd=[PY, "-c", "import time; time.sleep(60)"])


def test_ocr_pages_dem_dung(tmp_path):
    """PDF thật chỉ có ảnh (như bản scan) → mỗi trang là 1 trang scan."""
    from PIL import Image

    f = tmp_path / "scan.pdf"
    pages = [Image.new("RGB", (600, 800), "white") for _ in range(2)]
    pages[0].save(f, save_all=True, append_images=pages[1:])
    assert w.ocr_pages(f) == (2, "2 trang scan")
