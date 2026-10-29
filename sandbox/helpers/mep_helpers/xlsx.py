"""Xuất Excel theo quy cách mặc định: header đậm + nền, cố định dòng tiêu đề, định dạng số, sheet Nguồn.

    from mep_helpers import xlsx
    xlsx.write_table("/workspace/output/gio_tuoi.xlsx", headers=["Khu vực", "L/s.người"], rows=[["Phòng ngủ", 2.5]],
                     sources=citations, number_formats={"L/s.người": "0.0"})
"""
from __future__ import annotations

from typing import Iterable, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from mep_helpers import style
from mep_helpers.sources import SOURCE_HEADERS, source_row


def _font(bold: bool = False) -> Font:
    return Font(name=style.XLSX_FONT, size=style.XLSX_FONT_SIZE, bold=bold)


def style_header(ws: Worksheet, row: int = 1, fill: str = style.HEADER_FILL) -> None:
    side = Side(style="thin", color=style.BORDER_COLOR)
    for cell in ws[row]:
        if cell.value is None:
            continue
        cell.font = _font(bold=True)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(top=side, bottom=side, left=side, right=side)


def style_body(ws: Worksheet, first_row: int = 2) -> None:
    side = Side(style="thin", color=style.BORDER_COLOR)
    for row in ws.iter_rows(min_row=first_row):
        for cell in row:
            cell.font = _font(bold=cell.font.bold if cell.font else False)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(top=side, bottom=side, left=side, right=side)


def autofit(ws: Worksheet, min_width: int = 8, max_width: int = 60) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                longest = max(len(line) for line in str(cell.value).splitlines() or [""])
                widths[cell.column] = max(widths.get(cell.column, 0), longest)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(max_width, w + 2))


def highlight_row(ws: Worksheet, row: int, color: str = style.HIGHLIGHT_FILL) -> None:
    for cell in ws[row]:
        cell.fill = PatternFill("solid", fgColor=color)


def add_sources_sheet(wb: Workbook, sources: Iterable[dict], title: str = "Nguồn") -> Worksheet:
    ws = wb.create_sheet(title)
    ws.append(SOURCE_HEADERS)
    seen = set()
    for c in sources:
        row = tuple(source_row(c))
        if row not in seen:
            seen.add(row)
            ws.append(list(row))
    style_header(ws)
    style_body(ws)
    ws.freeze_panes = "A2"
    autofit(ws)
    return ws


def fill_table(ws: Worksheet, headers: Sequence[str], rows: Iterable[Sequence], start_row: int = 1,
               number_formats: dict[str, str] | None = None) -> None:
    """Ghi header + rows bắt đầu từ start_row, định dạng, cố định dòng tiêu đề."""
    for j, h in enumerate(headers, 1):
        ws.cell(start_row, j, h)
    n = start_row
    for n, r in enumerate(rows, start_row + 1):
        for j, v in enumerate(r, 1):
            ws.cell(n, j, v)
    style_header(ws, start_row)
    style_body(ws, start_row + 1)
    for h, fmt in (number_formats or {}).items():
        if h in headers:
            col = list(headers).index(h) + 1
            for i in range(start_row + 1, n + 1):
                ws.cell(i, col).number_format = fmt
    ws.freeze_panes = ws.cell(start_row + 1, 1).coordinate
    autofit(ws)


def write_table(path: str, headers: Sequence[str], rows: Iterable[Sequence], sheet: str = "Bảng",
                sources: Iterable[dict] | None = None, number_formats: dict[str, str] | None = None) -> str:
    """Tạo workbook mới 1 bảng (+ sheet Nguồn nếu có sources). → path."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    fill_table(ws, headers, rows, number_formats=number_formats)
    if sources:
        add_sources_sheet(wb, sources)
    wb.save(path)
    return path


def open_template(path: str) -> Workbook:
    """Mở file mẫu (giữ tên sheet, tiêu đề, định dạng của mẫu)."""
    return load_workbook(path)
