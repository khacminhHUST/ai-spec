"""Xuất Word theo quy cách mặc định: font mặc định (cả ký tự Đông Á), heading, bảng, trích dẫn nguồn cuối tài liệu.

    from mep_helpers import docx as mdocx
    doc = mdocx.new_document(font="Times New Roman", size=13)
    doc.add_heading("Yêu cầu cáp", level=1)
    mdocx.add_table(doc, ["Hạng mục", "Yêu cầu"], [["Bán kính uốn", "6 lần đường kính"]])
    mdocx.add_sources(doc, citations)
    doc.save("/workspace/output/cap.docx")
"""
from __future__ import annotations

from typing import Iterable, Sequence

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.ns import qn
from docx.shared import Pt

from mep_helpers import style
from mep_helpers.sources import source_text

_STYLES = ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Table Grid", "List Bullet", "List Number")


def set_style_font(doc: DocumentType, name: str, font: str, size: float | None = None) -> None:
    try:
        st = doc.styles[name]
    except KeyError:
        return
    st.font.name = font
    if size:
        st.font.size = Pt(size)
    rpr = st.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), font)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attr), None)


def apply_font(doc: DocumentType, font: str = style.DOCX_FONT, size: float = style.DOCX_FONT_SIZE) -> None:
    """Đặt font cho các style chính + mọi run đã có (heading giữ cỡ chữ riêng)."""
    for name in _STYLES:
        set_style_font(doc, name, font, size if name == "Normal" else None)
    for p in doc.paragraphs:
        for r in p.runs:
            r.font.name = font
            r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.font.name = font
                        r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font)


def new_document(font: str = style.DOCX_FONT, size: float = style.DOCX_FONT_SIZE,
                 template: str | None = None) -> DocumentType:
    doc = Document(template) if template else Document()
    for name in _STYLES:
        set_style_font(doc, name, font, size if name == "Normal" else None)
    return doc


def add_table(doc: DocumentType, headers: Sequence[str], rows: Iterable[Sequence], style_name: str = "Table Grid"):
    rows = [list(r) for r in rows]
    table = doc.add_table(rows=1, cols=len(headers))
    try:
        table.style = style_name
    except (KeyError, ValueError):
        pass
    for j, h in enumerate(headers):
        cell = table.rows[0].cells[j]
        cell.text = ""
        cell.paragraphs[0].add_run(str(h)).bold = True
    for r in rows:
        cells = table.add_row().cells
        for j, v in enumerate(r[: len(headers)]):
            cells[j].text = "" if v is None else str(v)
    return table


def add_sources(doc: DocumentType, sources: Iterable[dict], heading: str = "Nguồn tham khảo") -> None:
    """Danh sách nguồn cuối tài liệu: [n] Tài liệu · Rev · Mục · Trang."""
    doc.add_heading(heading, level=2)
    seen = set()
    for i, c in enumerate(sources, 1):
        text = source_text(c)
        if text and text not in seen:
            seen.add(text)
            doc.add_paragraph(f"[{c.get('n', i)}] {text}")
