"""Docling PDF → DoclingDocument, cache theo sha256 file + phiên bản tuỳ chọn (artifacts/_cache/docling/).

Westin (833 tr) parse mất nhiều phút → luôn đọc cache nếu có.
OCR chỉ bật khi PDF có trang scan (không có text layer nhưng có ảnh).
"""
from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

from shared.config import ROOT

log = logging.getLogger(__name__)
CACHE_DIR = ROOT / "artifacts" / "_cache" / "docling"
OPTIONS_VERSION = "v1"


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan_pages(path: str | Path, min_chars: int = 30) -> list[int]:
    """Trang (1-based) không có text layer nhưng có ảnh → cần OCR."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    out = []
    for i in range(len(pdf)):
        page = pdf[i]
        if len(page.get_textpage().get_text_range().strip()) >= min_chars:
            continue
        if any(o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for o in page.get_objects()):
            out.append(i + 1)
    return out


@lru_cache(maxsize=2)
def _converter(ocr: bool):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    po = PdfPipelineOptions(
        do_ocr=ocr,
        do_table_structure=True,
        accelerator_options=AcceleratorOptions(num_threads=min(16, os.cpu_count() or 4)),
    )
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=po)})


def convert(path: str | Path, file_hash: str | None = None, use_cache: bool = True):
    """→ (DoclingDocument, info{ocr, scan_pages, cached})."""
    from docling_core.types.doc import DoclingDocument

    path = Path(path)
    file_hash = file_hash or file_sha256(path)
    scans = scan_pages(path)
    ocr = bool(scans)
    cache = CACHE_DIR / f"{file_hash[:16]}_{OPTIONS_VERSION}_{'ocr' if ocr else 'txt'}.json"
    info = {"ocr": ocr, "scan_pages": scans, "cached": False}
    if use_cache and cache.exists():
        info["cached"] = True
        return DoclingDocument.load_from_json(cache), info
    log.info("Docling parse %s (ocr=%s)…", path.name, ocr)
    doc = _converter(ocr).convert(str(path)).document
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    doc.save_as_json(cache)
    return doc, info


if __name__ == "__main__":  # python -m ingestion.docling_cache <pdf>... : làm nóng cache
    import sys
    import time

    logging.basicConfig(level=logging.INFO)
    for p in sys.argv[1:]:
        t = time.time()
        _, inf = convert(p)
        print(p, inf, f"{time.time() - t:.0f}s", flush=True)
