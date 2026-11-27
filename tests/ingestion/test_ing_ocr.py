"""Unit: OCR cho PDF scan / lỗi font (app.services.ingestion.docling_cache) – không gọi Docling, không cần Tesseract."""
from __future__ import annotations

import pytest

from app.services.ingestion import docling_cache as dc

pytestmark = pytest.mark.unit

GOOD_VI = ("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM. Độc lập - Tự do - Hạnh phúc. Hợp đồng thi công xây dựng công trình, "
           "nhiệt độ 25°C ± 2°C, tiết diện cáp 2,5 mm², công suất 15 kW, tần số 50 Hz, theo TCVN 9206:2012 và QCVN 06. ")
GOOD_EN = ("Director of Services (optional): 9.3 m² (100 ft²) - private. Housekeeping Manager: 7.4 m² (80 ft²). "
           "Linen storage shall be located adjacent to the service elevator lobby with 1200 mm clear width. ")


# ---------------------------------------------------------------- phát hiện lỗi font

@pytest.mark.parametrize("text", [GOOD_VI * 3, GOOD_EN * 3, ("• Lắp đặt máng cáp " + chr(0xF0B7) + " đúng cao độ. ") * 10])
def test_text_ok_not_flagged(text):
    assert dc.text_broken(text) is False


@pytest.mark.parametrize("text", [
    # thiếu ToUnicode → chữ có dấu rơi mất (mẫu thật từ hợp đồng Taseco)
    "CNG HÒA XÃ HI CHÛ NGHÎA VIT NAM Đc lp - T do - Hnh phúc. THA THUN PH LUC HP ĐÒNG (B sung, điu chīnh phm vi "
    "dch v tin ich – Thu gom và vn chuyn ph thi). Căn c B lut Dân s s 91/2015/QH13 và các quy đnh pháp lut "
    "hin hành; Căn c nhu cu và kh năng ca các bên, hai bên thng nht ký kt",
    # font TCVN3 (ABC) đọc như Latin-1
    "Céng hoµ x· héi chñ nghÜa ViÖt Nam. §éc lËp - Tù do - H¹nh phóc. Hîp ®ång thi c«ng x©y dùng c«ng tr×nh " * 2,
    # font VNI đọc như Latin-1
    "Coäng hoøa xaõ hoäi chuû nghóa Vieät Nam. Ñoäc laäp - Töï do - Haïnh phuùc. Hôïp ñoàng thi coâng " * 2,
    # glyph không có mã Unicode (vùng riêng PUA)
    ("C" + chr(0xE123) + "ng h" + chr(0xE045) + "a x" + chr(0xE011) + " h" + chr(0xE123) + "i ") * 10,
])
def test_text_broken_flagged(text):
    assert dc.text_broken(text) is True


# Bảng tính kỹ thuật có text layer tốt – ký hiệu không nguyên âm (hdd, Hhh, Wx, VRd, mg…) từng bị báo nhầm
# "lỗi font" → OCR lại cả file (F2HH_TM MEP 205 tr, GBIA1.1 4001 353 tr bị từ chối – 26/11/2026).
CALC_VI = ("Tính toán bơm sự cố cho bể tự hoại. H = Hhh + hb + hdd + hcb + htd + hdp, L = 4 m. Hhh = 4 m, hb = 2 m, "
           "hdd = i x l = 0.06 m, hcb = 30% hdd, htd = 1.5 m, hdp = 3 m. Qb = 25 m3/h, Qngđ = 96 m3, ống đẩy bơm D110. "
           "Chọn bơm có lưu lượng Qb và cột áp H theo bảng tính. Wbn, Wbth, Wbm, qtt, qdc, Nnh, Ncq, Stt, Vt. ")
CALC_EN = ("Fc Compressive force. VEd Resultant of shear forces Vy, Vz in bolt. MEd Bending moment. VRd,c Design "
           "resistance in case of concrete cone failure under shear load - EN1992-4 - Cl. 7.2.2.5. VRd,cp Design "
           "resistance in case of concrete pryout failure. ULS41 1.1 SW +1.2 SDL +1.89 Wx+1, ULS42 Wx+2, ULS43 Wx-1, "
           "ULS44 Wx-2, Ncr, Lcr, ds, Ltb, BxH, kNm, mg, mg, mg, bt, bt, ln, ln. ")


@pytest.mark.parametrize("text", [CALC_VI * 2, CALC_EN * 2])
def test_engineering_symbols_not_flagged(text):
    assert dc.text_broken(text) is False


def test_short_text_never_flagged():
    assert dc.text_broken("Đc lp") is False


@pytest.mark.parametrize("broken,n_text,expected", [
    ([], 50, False),
    ([612], 833, False),  # 1 trang lạ trong tài liệu lớn → không OCR lại cả file
    ([1], 4, True),
    (list(range(1, 11)), 100, True),
    (list(range(1, 9)), 100, False),
])
def test_needs_full_ocr(broken, n_text, expected):
    assert dc.needs_full_ocr(broken, n_text) is expected


# ---------------------------------------------------------------- chọn bộ OCR

def test_auto_falls_back_to_easyocr_without_tesseract(monkeypatch):
    monkeypatch.delenv("MEP_OCR_ENGINE", raising=False)
    monkeypatch.delenv("MEP_OCR_LANG", raising=False)
    monkeypatch.setattr(dc, "_tesseract_cmd", lambda: None)
    assert dc.ocr_engine() == "easyocr"
    o = dc._ocr_options(False)
    assert type(o).__name__ == "EasyOcrOptions" and o.lang == ["vi", "en"]


def test_auto_prefers_tesseract_when_installed(monkeypatch, tmp_path):
    for lg in ("vie", "eng"):
        (tmp_path / f"{lg}.traineddata").write_bytes(b"x")
    monkeypatch.delenv("MEP_OCR_ENGINE", raising=False)
    monkeypatch.delenv("MEP_OCR_LANG", raising=False)
    monkeypatch.setenv("MEP_TESSDATA", str(tmp_path))
    monkeypatch.setattr(dc, "_tesseract_cmd", lambda: "tesseract.exe")
    assert dc.ocr_engine() == "tesseract"


def test_tesseract_forced_but_missing_raises_clear_error(monkeypatch):
    monkeypatch.setenv("MEP_OCR_ENGINE", "tesseract")
    monkeypatch.setattr(dc, "_tesseract_cmd", lambda: None)
    with pytest.raises(RuntimeError, match="Tesseract"):
        dc._ocr_options(False)


def test_tesseract_options_vietnamese(monkeypatch, tmp_path):
    for lg in ("vie", "eng"):
        (tmp_path / f"{lg}.traineddata").write_bytes(b"x")
    monkeypatch.setenv("MEP_OCR_ENGINE", "tesseract")
    monkeypatch.setenv("MEP_TESSDATA", str(tmp_path))
    monkeypatch.delenv("MEP_OCR_LANG", raising=False)
    monkeypatch.setattr(dc, "_tesseract_cmd", lambda: "tesseract.exe")
    o = dc._ocr_options(True)
    assert o.lang == ["vie", "eng"] and o.path == str(tmp_path) and o.mode.value == "full_page"


def test_missing_language_data_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("MEP_OCR_ENGINE", "tesseract")
    monkeypatch.setenv("MEP_TESSDATA", str(tmp_path))
    monkeypatch.setattr(dc, "_tesseract_cmd", lambda: "tesseract.exe")
    with pytest.raises(RuntimeError, match="vie"):
        dc._ocr_options(False)


def test_invalid_engine(monkeypatch):
    monkeypatch.setenv("MEP_OCR_ENGINE", "foo")
    with pytest.raises(ValueError):
        dc.ocr_engine()


# ---------------------------------------------------------------- cache

def test_text_pdf_keeps_old_cache_key(monkeypatch, tmp_path):
    """File có chữ chuẩn giữ đúng tên cache cũ → không parse lại bộ tài liệu đã có (Westin 833 tr)."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(dc, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(dc, "analyze_pages", lambda p: ([], [], 10))
    seen = {}

    class FakeDoc:
        @staticmethod
        def load_from_json(path):
            seen["path"] = path
            return "doc"

    import docling_core.types.doc as dt
    monkeypatch.setattr(dt, "DoclingDocument", FakeDoc)
    (tmp_path / "0123456789abcdef_v1_txt.json").write_text("{}")
    doc, info = dc.convert(pdf, file_hash="0123456789abcdef" + "0" * 48)
    assert doc == "doc" and info["cached"] and info["ocr"] is False
    assert seen["path"].name == "0123456789abcdef_v1_txt.json"


def test_ocr_cache_key_separate_from_old_rapidocr(monkeypatch, tmp_path):
    """Bản OCR cũ (RapidOCR, mất dấu) không được dùng lại."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(dc, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(dc, "analyze_pages", lambda p: ([1, 2], [], 0))
    monkeypatch.setenv("MEP_OCR_ENGINE", "easyocr")
    (tmp_path / "0123456789abcdef_v1_ocr.json").write_text("{}")  # cache RapidOCR cũ

    class Conv:
        def convert(self, _):
            class R:
                class document:
                    @staticmethod
                    def save_as_json(path):
                        path.write_text("{}")
            return R

    monkeypatch.setattr(dc, "_converter", lambda ocr, full=False: Conv())
    _, info = dc.convert(pdf, file_hash="0123456789abcdef" + "0" * 48)
    assert info["ocr"] and not info["cached"] and info["ocr_engine"] == "easyocr"
    assert (tmp_path / f"0123456789abcdef_{dc.OCR_VERSION}-easyocr.json").exists()


# ---------------------------------------------------------------- thứ tự đọc EasyOCR

def _box(x0, y0, x1, y1, text):
    return ([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.9)


def test_reading_order_moves_tilted_words_back_into_line():
    """EasyOCR trả ô nghiêng (chữ có dấu cao) sau cùng → phải đưa về đúng vị trí trong dòng."""
    res = [
        _box(0, 10, 50, 30, "Độc"), _box(60, 10, 100, 30, "lập"), _box(160, 10, 190, 30, "do"),
        _box(0, 50, 60, 70, "Hạnh"), _box(70, 50, 120, 70, "phúc"),
        _box(120, 6, 150, 31, "Tự"),  # ô nghiêng, cao hơn vì dấu – bị EasyOCR đẩy xuống cuối
    ]
    assert [r[1] for r in dc.reading_order(res)] == ["Độc", "lập", "Tự", "do", "Hạnh", "phúc"]


def test_reading_order_keeps_separate_lines():
    res = [_box(0, 40, 50, 60, "b"), _box(0, 0, 50, 20, "a"), _box(60, 41, 90, 61, "c")]
    assert [r[1] for r in dc.reading_order(res)] == ["a", "b", "c"]
