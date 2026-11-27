"""Docling PDF → DoclingDocument, cache theo sha256 file + phiên bản tuỳ chọn (artifacts/_cache/docling/).

Westin (833 tr) parse mất nhiều phút → luôn đọc cache nếu có.

OCR bật khi PDF có:
- trang scan (không có text layer nhưng có ảnh), hoặc
- trang "lỗi font": có text layer nhưng chữ hỏng (font TCVN3/VNI cũ, thiếu bảng ToUnicode → mất dấu,
  ký tự vùng riêng PUA…). Khi đó OCR toàn trang, bỏ text layer hỏng.

Bộ OCR (chỉ thay phần nhận dạng chữ của Docling; bố cục/bảng giữ nguyên) – MEP_OCR_ENGINE:
- `auto` (mặc định): Tesseract nếu máy đã cài, ngược lại EasyOCR.
- `easyocr`: gói Python (requirements.txt), ngôn ngữ `vi+en`, tự tải mô hình lần đầu. Không cần cài gì thêm.
- `tesseract`: Tesseract CLI `vie+eng` – nhanh hơn trên CPU, cần cài phần mềm hệ thống.
- `rapidocr`: mặc định cũ của Docling, KHÔNG đọc được dấu tiếng Việt (chỉ để so sánh).
Biến khác: MEP_OCR_LANG, MEP_TESSERACT_CMD, MEP_TESSDATA (mặc định <repo>/data/tessdata), MEP_EASYOCR_DIR.
File không cần OCR giữ nguyên đường xử lý và cache cũ.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from app.core.config import ROOT

log = logging.getLogger(__name__)
CACHE_DIR = ROOT / "artifacts" / "_cache" / "docling"
OPTIONS_VERSION = "v1"  # cache bản text (không OCR) – giữ nguyên để không parse lại bộ tài liệu cũ
OCR_VERSION = "ocr3"  # cache bản OCR – tăng khi đổi bộ OCR / tuỳ chọn OCR

# ---------------------------------------------------------------- phát hiện lỗi font

# Chữ Latin-1 hợp lệ trong tiếng Việt; phần còn lại của dải U+00A0–U+00FF xuất hiện dày đặc là dấu hiệu
# font TCVN3 (¸ µ ¶ · ¹ ¨ © ª « ¬ ® …) hoặc VNI (ä ï ö ø ñ …) bị đọc như Latin-1.
_VI_LATIN1 = set("ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúý")
_OK_SYMBOLS = set("°±²³×÷·µ«»©®§¼½¾")  # ký hiệu kỹ thuật/trích dẫn hay gặp – chỉ tính khi quá dày
_VOWELS = set("aeiouyAEIOUY" "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ"
              "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Đơn vị / viết tắt kỹ thuật không có nguyên âm – không tính là "từ mất dấu".
_UNITS = {"mm", "cm", "km", "ft", "kg", "kw", "kva", "kvar", "kwh", "mw", "hz", "khz", "db", "dba", "pa", "kpa",
          "mpa", "psi", "lm", "lx", "cd", "nm", "mph", "rpm", "cfm", "pt", "px", "st", "nd", "rd", "th", "vs",
          "dr", "mr", "mrs", "ms", "hr", "hrs", "min", "sq", "pcs", "hp", "kn", "ml", "lb", "lbs", "qty", "pp"}
# Âm tiết tiếng Việt rơi mất nguyên âm có dấu chỉ còn [phụ âm đầu][phụ âm cuối]: "Độc lập" → "Đc lp", "nhất" → "nht".
# Ký hiệu kỹ thuật (hdd, Hhh, Wx, mg, VRd, kNm, qtt…) không có dạng này → không tính (báo nhầm cả file → OCR thừa).
_VI_FRAGMENT = re.compile(r"(?:ngh|ng|nh|ch|gh|gi|kh|ph|th|tr|[bcdđghklmnprstvx])?(?:ng|nh|ch|[cmnpt])?",
                          re.IGNORECASE)


def text_broken(text: str) -> bool:
    """Text layer của 1 trang có bị hỏng font không (heuristic, ưu tiên không báo nhầm)."""
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 40:
        return False
    odd = sum(
        1
        for c in chars
        # vùng riêng (PUA): glyph không có mã Unicode; trừ F000–F0FF = bullet/ký hiệu font Symbol của Word
        if ("\ue000" <= c <= "\uf8ff" and not "\uf000" <= c <= "\uf0ff")
        or c == "\ufffd"
        or (ord(c) < 32)
        or ("\u00a0" <= c <= "\u00ff" and c not in _VI_LATIN1 and c not in _OK_SYMBOLS)
    )
    sym = sum(1 for c in chars if c in _OK_SYMBOLS)
    if odd / len(chars) > 0.02 or sym / len(chars) > 0.08:
        return True
    # Thiếu ToUnicode cho chữ có dấu → chữ bị rơi mất: "Độc lập" → "Đc lp". Mọi âm tiết tiếng Việt đều có
    # nguyên âm, nên tỉ lệ từ thường (có chữ thường) mà không có nguyên âm cao là bất thường.
    words = [w for w in _WORD.findall(text)
             if len(w) >= 2 and w.isalpha() and any(ch.islower() for ch in w) and w.lower() not in _UNITS]
    if len(words) < 25:
        return False
    # Đếm theo từ khác nhau: chữ rơi dấu thật thì đa dạng, ký hiệu (bt, ln, Wx…) thì lặp lại cả trang.
    distinct = {w.lower() for w in words}
    novowel = {w.lower() for w in words if not any(ch in _VOWELS for ch in w) and _vi_fragment(w)}
    return len(novowel) / len(distinct) > 0.12


def _vi_fragment(w: str) -> bool:
    """Từ không nguyên âm trông như âm tiết Việt bị rơi dấu: chữ thường hoặc Hoa đầu, dạng phụ âm đầu + cuối."""
    return w[1:] == w[1:].lower() and _VI_FRAGMENT.fullmatch(w) is not None


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def analyze_pages(path: str | Path, min_chars: int = 30) -> tuple[list[int], list[int], int]:
    """→ (trang scan, trang lỗi font, số trang có text layer), số trang 1-based."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    scans, broken, n_text = [], [], 0
    for i in range(len(pdf)):
        page = pdf[i]
        text = page.get_textpage().get_text_range()
        if len(text.strip()) >= min_chars:
            n_text += 1
            if text_broken(text):
                broken.append(i + 1)
            continue
        if any(o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for o in page.get_objects()):
            scans.append(i + 1)
    return scans, broken, n_text


def needs_full_ocr(broken: list[int], n_text: int) -> bool:
    """Lỗi font đủ nhiều (≥ 10% trang có chữ) mới OCR lại cả file – 1 trang lạ trong tài liệu 800 trang thì bỏ qua."""
    return bool(broken) and len(broken) >= max(1, 0.1 * n_text)


def scan_pages(path: str | Path, min_chars: int = 30) -> list[int]:
    """Trang (1-based) không có text layer nhưng có ảnh → cần OCR."""
    return analyze_pages(path, min_chars)[0]


# ---------------------------------------------------------------- bộ OCR

ENGINES = ("auto", "tesseract", "easyocr", "rapidocr")


def ocr_engine() -> str:
    """Bộ OCR thực dùng. `auto` (mặc định): Tesseract nếu máy đã cài + có dữ liệu tiếng Việt, ngược lại EasyOCR
    (gói Python trong requirements.txt – luôn có sẵn sau `pip install -r requirements.txt`)."""
    engine = os.environ.get("MEP_OCR_ENGINE", "auto").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"MEP_OCR_ENGINE không hợp lệ: {engine!r} ({' | '.join(ENGINES)})")
    if engine == "auto":
        return "tesseract" if _tesseract_cmd() and _tessdata_ok(_ocr_langs("tesseract")) else "easyocr"
    return engine


def _ocr_langs(engine: str) -> list[str]:
    default = "vie+eng" if engine == "tesseract" else "vi+en"
    return [x for x in re.split(r"[+,\s]+", os.environ.get("MEP_OCR_LANG", default)) if x]


def _tesseract_cmd() -> str | None:
    cand = [os.environ.get("MEP_TESSERACT_CMD"), shutil.which("tesseract"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
    return next((c for c in cand if c and Path(c).is_file()), None)


def _tessdata() -> str | None:
    p = os.environ.get("MEP_TESSDATA") or str(ROOT / "data" / "tessdata")
    return p if Path(p).is_dir() else None


def _tessdata_ok(langs: list[str]) -> bool:
    d = _tessdata()
    return d is None or all((Path(d) / f"{lg}.traineddata").is_file() for lg in langs)


def reading_order(results):
    """Sắp kết quả EasyOCR theo thứ tự đọc: gom dòng theo độ chồng dọc, trong dòng xếp trái → phải.

    EasyOCR trả các ô "nằm ngang" trước rồi mới tới ô hơi nghiêng (dấu tiếng Việt như Ự, Ợ làm ô nghiêng)
    → Docling ghép chữ theo thứ tự đó nên từ có dấu bị đẩy xuống cuối đoạn ("Hợp đồng" ra cuối dòng)."""
    items = []
    for r in results:
        pts = r[0]
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        items.append((min(ys), max(ys), min(xs), r))
    items.sort(key=lambda t: (t[0] + t[1]) / 2)
    lines: list[list] = []
    for it in items:
        top, bot = it[0], it[1]
        if lines:
            ltop = min(x[0] for x in lines[-1])
            lbot = max(x[1] for x in lines[-1])
            overlap = min(bot, lbot) - max(top, ltop)
            if overlap >= 0.5 * min(bot - top, lbot - ltop):
                lines[-1].append(it)
                continue
        lines.append([it])
    return [it[3] for line in lines for it in sorted(line, key=lambda t: t[2])]


def _install_easyocr_reading_order() -> None:
    """Bọc easyocr.Reader.readtext (1 lần) để Docling nhận ô chữ đúng thứ tự đọc."""
    import easyocr

    if getattr(easyocr.Reader.readtext, "_mep_reading_order", False):
        return
    orig = easyocr.Reader.readtext

    def readtext(self, *args, **kwargs):
        res = orig(self, *args, **kwargs)
        if res and isinstance(res[0], (list, tuple)) and len(res[0]) == 3:
            return reading_order(res)
        return res

    readtext._mep_reading_order = True
    easyocr.Reader.readtext = readtext


def _mode(force_full_page: bool):
    from docling.datamodel.pipeline_options import OcrMode

    return OcrMode.FULL_PAGE if force_full_page else OcrMode.DEFAULT


def _ocr_options(force_full_page: bool):
    engine = ocr_engine()
    mode = _mode(force_full_page)
    if engine == "rapidocr":  # mặc định cũ của Docling – không đọc được dấu tiếng Việt
        from docling.datamodel.pipeline_options import RapidOcrOptions

        return RapidOcrOptions(mode=mode)
    if engine == "easyocr":
        from docling.datamodel.pipeline_options import EasyOcrOptions

        _install_easyocr_reading_order()
        store = os.environ.get("MEP_EASYOCR_DIR")  # mặc định ~/.EasyOCR (tự tải mô hình lần đầu, ~100 MB)
        return EasyOcrOptions(lang=_ocr_langs("easyocr"), mode=mode, model_storage_directory=store)
    from docling.datamodel.pipeline_options import TesseractCliOcrOptions

    cmd = _tesseract_cmd()
    if not cmd:
        raise RuntimeError("MEP_OCR_ENGINE=tesseract nhưng chưa cài Tesseract (hoặc đặt MEP_TESSERACT_CMD). "
                           "Bỏ biến này (auto) để dùng EasyOCR có sẵn.")
    langs = _ocr_langs("tesseract")
    if not _tessdata_ok(langs):
        raise RuntimeError(f"Thiếu dữ liệu ngôn ngữ OCR {langs} trong {_tessdata()}")
    opts = dict(lang=langs, tesseract_cmd=cmd, mode=mode)
    if _tessdata():
        opts["path"] = _tessdata()
    return TesseractCliOcrOptions(**opts)


@lru_cache(maxsize=4)
def _converter(ocr: bool, force_full_page: bool = False):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    kw = {"ocr_options": _ocr_options(force_full_page)} if ocr else {}
    po = PdfPipelineOptions(
        do_ocr=ocr,
        do_table_structure=True,
        accelerator_options=AcceleratorOptions(num_threads=min(16, os.cpu_count() or 4)),
        **kw,
    )
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=po)})


def convert(path: str | Path, file_hash: str | None = None, use_cache: bool = True):
    """→ (DoclingDocument, info{ocr, ocr_engine, scan_pages, broken_font_pages, cached})."""
    from docling_core.types.doc import DoclingDocument

    path = Path(path)
    file_hash = file_hash or file_sha256(path)
    scans, broken, n_text = analyze_pages(path)
    force_full = needs_full_ocr(broken, n_text)  # text layer hỏng → OCR cả trang thay vì chỉ vùng ảnh
    ocr = bool(scans) or force_full
    if ocr:
        tag = f"{OCR_VERSION}-{ocr_engine()}{'-full' if force_full else ''}"
    else:
        tag = f"{OPTIONS_VERSION}_txt"
    cache = CACHE_DIR / f"{file_hash[:16]}_{tag}.json"
    info = {"ocr": ocr, "ocr_engine": ocr_engine() if ocr else None, "scan_pages": scans,
            "broken_font_pages": broken, "cached": False}
    if use_cache and cache.exists():
        info["cached"] = True
        return DoclingDocument.load_from_json(cache), info
    log.info("Docling parse %s (ocr=%s, engine=%s, scan=%d tr, lỗi font=%d tr)…", path.name, ocr,
             info["ocr_engine"], len(scans), len(broken))
    doc = _converter(ocr, force_full).convert(str(path)).document
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    doc.save_as_json(cache)
    return doc, info


if __name__ == "__main__":  # python -m app.services.ingestion.docling_cache <pdf>... : làm nóng cache
    import sys
    import time

    logging.basicConfig(level=logging.INFO)
    for p in sys.argv[1:]:
        t = time.time()
        _, inf = convert(p)
        print(p, inf, f"{time.time() - t:.0f}s", flush=True)
