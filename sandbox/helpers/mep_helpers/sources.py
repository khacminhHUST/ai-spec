"""Chuẩn hóa nguồn trích dẫn (citation của search_project_docs) → cột/sheet Nguồn."""

SOURCE_HEADERS = ["Tài liệu", "Rev", "Mục", "Trang"]


def page_text(c: dict) -> str:
    p, pe = c.get("page"), c.get("page_end")
    if p is None:
        return ""
    return f"{p}-{pe}" if pe and pe != p else str(p)


def section_text(c: dict) -> str:
    num, title = c.get("number"), c.get("title")
    if num and title:
        return f"{num} {title}"
    return str(num or title or c.get("section_key") or "")


def source_row(c: dict) -> list[str]:
    """citation dict → [Tài liệu, Rev, Mục, Trang]."""
    return [str(c.get("doc_title") or c.get("doc_id") or ""), str(c.get("revision") or ""), section_text(c),
            page_text(c)]


def source_text(c: dict) -> str:
    """'Tài liệu · Rev · Mục · Trang' (bỏ phần trống) – dùng cho 1 ô 'Nguồn'."""
    doc, rev, sec, page = source_row(c)
    parts = [doc, f"Rev {rev}" if rev else "", f"Mục {sec}" if sec else "", f"Tr. {page}" if page else ""]
    return " · ".join(p for p in parts if p)
