"""Kiểm tra file sau khi tạo: mở lại được, số dòng/cột, ô lỗi, font… → dict báo cáo (in ra để agent đọc).

    from mep_helpers import check
    print(check.check_file("/workspace/output/a.xlsx", min_rows=5))
"""
from __future__ import annotations

import os
import re

ERROR_VALUES = {"#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NUM!", "#NULL!"}


def check_xlsx(path: str, min_rows: int = 1, expect_headers: list[str] | None = None) -> dict:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    report = {"file": path, "ok": True, "problems": [], "sheets": []}
    for ws in wb.worksheets:
        errors = [c.coordinate for row in ws.iter_rows() for c in row
                  if isinstance(c.value, str) and c.value.strip() in ERROR_VALUES]
        headers = [c.value for c in ws[1]] if ws.max_row >= 1 else []
        report["sheets"].append({"name": ws.title, "rows": ws.max_row, "cols": ws.max_column, "headers": headers,
                                 "error_cells": errors[:20]})
        if errors:
            report["problems"].append(f"{ws.title}: ô lỗi {errors[:5]}")
    main = report["sheets"][0] if report["sheets"] else None
    if not main or main["rows"] - 1 < min_rows:
        report["problems"].append(f"Sheet đầu có ít hơn {min_rows} dòng dữ liệu")
    if expect_headers and main:
        missing = [h for h in expect_headers if h not in (main["headers"] or [])]
        if missing:
            report["problems"].append(f"Thiếu cột: {missing}")
    report["ok"] = not report["problems"]
    return report


def check_docx(path: str, expect_font: str | None = None, expect_text: list[str] | None = None) -> dict:
    from docx import Document

    doc = Document(path)
    texts = [p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells]
    full = "\n".join(texts)
    fonts = {doc.styles["Normal"].font.name} | {r.font.name for p in doc.paragraphs for r in p.runs if r.font.name}
    report = {"file": path, "paragraphs": len(doc.paragraphs), "tables": len(doc.tables),
              "fonts": sorted(f for f in fonts if f), "problems": []}
    if not full.strip():
        report["problems"].append("Tài liệu rỗng")
    if expect_font and any(f != expect_font for f in report["fonts"]):
        report["problems"].append(f"Font khác {expect_font}: {report['fonts']}")
    for t in expect_text or []:
        if t not in full:
            report["problems"].append(f"Thiếu nội dung: {t}")
    report["ok"] = not report["problems"]
    return report


def check_pdf(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    problems = [] if data.startswith(b"%PDF") and pages else ["Không phải PDF hợp lệ"]
    return {"file": path, "pages": pages, "size": len(data), "problems": problems, "ok": not problems}


def check_file(path: str, **kw) -> dict:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {"file": path, "ok": False, "problems": ["File không tồn tại hoặc rỗng"]}
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            return check_xlsx(path, **kw)
        if ext == ".docx":
            return check_docx(path, **kw)
        if ext == ".pdf":
            return check_pdf(path)
    except Exception as e:  # noqa: BLE001
        return {"file": path, "ok": False, "problems": [f"Không mở được: {e}"]}
    return {"file": path, "ok": True, "size": os.path.getsize(path), "problems": []}
