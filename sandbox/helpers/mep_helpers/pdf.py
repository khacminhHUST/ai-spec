"""PDF: chuyển docx/xlsx → pdf bằng LibreOffice headless; hoặc vẽ trực tiếp bằng reportlab với font tiếng Việt."""
from __future__ import annotations

import os
import subprocess

from mep_helpers import style


def convert(path: str, outdir: str = "/workspace/output", fmt: str = "pdf", timeout_s: int = 90) -> str:
    """docx/xlsx/pptx → pdf (hoặc định dạng LibreOffice khác). → đường dẫn file mới."""
    os.makedirs(outdir, exist_ok=True)
    subprocess.run(["soffice", "--headless", "--norestore", "--convert-to", fmt, "--outdir", outdir, path],
                   check=True, capture_output=True, timeout=timeout_s)
    out = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + "." + fmt)
    if not os.path.exists(out):
        raise RuntimeError(f"LibreOffice không tạo được {out}")
    return out


def register_vn_fonts() -> str:
    """Đăng ký DejaVuSans cho reportlab. → tên font thường dùng trong setFont / ParagraphStyle."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, path in style.PDF_FONTS.items():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
    return "DejaVuSans"
