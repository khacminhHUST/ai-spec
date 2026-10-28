"""Bước 2 – Parse: PDF → danh sách Block (heading/text/list/table/caption), mỗi khối có page + bbox.

- Docling (cache ở app.services.ingestion.docling_cache). OCR tự bật khi có trang scan.
- Bỏ header/footer: lớp furniture của Docling + khối lặp lại trên nhiều trang (config › header_footer).
- Toạ độ bbox: gốc trên-trái, y hướng xuống (pt).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services.ingestion import docling_cache
from app.services.ingestion.textutil import ingestion_cfg, normalize_text

LABEL_KIND = {
    "section_header": "heading", "title": "heading",
    "text": "text", "paragraph": "text", "reference": "text", "footnote": "text", "code": "text", "formula": "text",
    "list_item": "list", "caption": "caption", "table": "table", "document_index": "toc",
    "picture": "picture", "chart": "picture", "key_value_region": "text", "form": "text",
    "checkbox_selected": "text", "checkbox_unselected": "text",
}


@dataclass
class Cell:
    r0: int
    r1: int
    c0: int
    c1: int
    text: str
    bbox: tuple[float, float, float, float]  # l, t, r, b (top-left)
    header: bool = False


@dataclass
class Block:
    kind: str                       # heading | text | list | table | caption | toc | picture
    text: str
    page: int
    bbox: tuple[float, float, float, float]
    label: str = ""                 # nhãn Docling gốc
    lang: str | None = None
    page_end: int | None = None
    rows: list[list[str]] | None = None    # bảng: lưới text (đã tách theo ngôn ngữ nếu song ngữ)
    header_rows: int = 0
    cells: list[Cell] | None = None
    number: str | None = None       # heading: số mục (sau khi nhận diện)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def last_page(self) -> int:
        return self.page_end or self.page

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("cells", None)
        return {k: v for k, v in d.items() if v not in (None, {}, [])}


@dataclass
class ParsedDoc:
    path: Path
    file_hash: str
    n_pages: int
    page_sizes: dict[int, tuple[float, float]]
    blocks: list[Block]
    info: dict[str, Any]
    removed: list[Block] = field(default_factory=list)
    furniture: list[Block] = field(default_factory=list)   # header/footer Docling (chỉ để loại trừ vùng)


def _bbox_tl(bbox, page_h: float) -> tuple[float, float, float, float]:
    if getattr(bbox.coord_origin, "name", "") == "BOTTOMLEFT":
        return (bbox.l, page_h - bbox.t, bbox.r, page_h - bbox.b)
    return (bbox.l, bbox.t, bbox.r, bbox.b)


def _table_block(item, page: int, bbox, page_h: float) -> Block:
    data = item.data
    cells = []
    for c in data.table_cells:
        cb = _bbox_tl(c.bbox, page_h) if c.bbox else bbox
        cells.append(Cell(c.start_row_offset_idx, c.end_row_offset_idx, c.start_col_offset_idx, c.end_col_offset_idx,
                          normalize_text(c.text).replace("\n", " "), cb, bool(c.column_header)))
    rows = [[normalize_text(c.text).replace("\n", " ") for c in row] for row in data.grid]
    header_rows = 0
    for row in data.grid:
        if row and all(c.column_header for c in row if c.text.strip()) and any(c.text.strip() for c in row):
            header_rows += 1
        else:
            break
    return Block("table", "", page, bbox, label="table", rows=rows, header_rows=header_rows, cells=cells)


def furniture_blocks(doc, sizes) -> list[Block]:
    out = []
    for it in doc.texts:
        if getattr(it.content_layer, "value", "body") == "body" or not it.prov:
            continue
        p = it.prov[0]
        out.append(Block("furniture", normalize_text(it.text), p.page_no,
                         _bbox_tl(p.bbox, sizes.get(p.page_no, (0, 842))[1]), label=it.label.value))
    return out


def docling_blocks(doc) -> tuple[list[Block], dict[int, tuple[float, float]]]:
    sizes = {int(no): (p.size.width, p.size.height) for no, p in doc.pages.items()}
    blocks: list[Block] = []
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        label = item.label.value if hasattr(item.label, "value") else str(item.label)
        kind = LABEL_KIND.get(label, "text")
        page = prov[0].page_no
        h = sizes.get(page, (0, 842))[1]
        bbox = _bbox_tl(prov[0].bbox, h)
        if kind == "table":
            blk = _table_block(item, page, bbox, h)
            if not any(c.strip() for r in blk.rows or [] for c in r):
                # bảng rỗng (Docling nhận nhầm hình/mặt bằng) → coi như hình, không tạo parent
                blocks.append(Block("picture", "", page, bbox, label="empty_table"))
                continue
            if len(prov) > 1:
                blk.page_end = prov[-1].page_no
            blocks.append(blk)
            continue
        if kind == "picture":
            blocks.append(Block("picture", "", page, bbox, label=label))
            continue
        raw = getattr(item, "orig", None) or getattr(item, "text", "") or ""
        marker = getattr(item, "marker", "") if label == "list_item" else ""
        if marker and raw.lstrip().startswith(marker):
            marker = ""
        if len(prov) > 1:
            # 1 item nhiều vùng (VD Docling gộp đoạn 2 cột / 2 trang) → mỗi vùng 1 khối, text theo charspan
            base = getattr(item, "text", "") or raw
            for k, pv in enumerate(prov):
                s, e = (pv.charspan or (0, len(base)))
                part = normalize_text(base[s:e]).replace("\n", " ")
                if k == 0 and marker:
                    part = f"{marker} {part}"
                if part:
                    hp = sizes.get(pv.page_no, (0, 842))[1]
                    blocks.append(Block(kind, part, pv.page_no, _bbox_tl(pv.bbox, hp), label=label,
                                        meta={"multi_prov": len(prov)}))
            continue
        if marker:
            raw = f"{marker} {raw}"
        text = normalize_text(raw).replace("\n", " ")
        if not text:
            continue
        blocks.append(Block(kind, text, page, bbox, label=label))
    return blocks, sizes


_DIGITS = re.compile(r"\d+")


def remove_headers_footers(blocks: list[Block], sizes: dict[int, tuple[float, float]], n_pages: int):
    """Khối text lặp lại (bỏ chữ số) trên nhiều trang → header/footer."""
    cfg = ingestion_cfg()["header_footer"]
    pages_by_key: dict[str, set[int]] = defaultdict(set)

    def key(b: Block) -> str:
        return _DIGITS.sub("#", b.text.lower()).strip()

    def in_margin(b: Block) -> bool:
        h = sizes.get(b.page, (0, 842))[1]
        m = cfg["margin_ratio"] * h
        return b.bbox[3] <= m or b.bbox[1] >= h - m

    for b in blocks:
        if b.kind in ("text", "heading", "caption", "list") and len(b.text) < 200:
            pages_by_key[key(b)].add(b.page)
    keep, removed = [], []
    for b in blocks:
        if b.kind in ("text", "heading", "caption", "list") and len(b.text) < 200:
            n = len(pages_by_key[key(b)])
            if (in_margin(b) and n >= cfg["min_repeat_pages"]) or n >= max(cfg["min_repeat_pages"], cfg["min_repeat_ratio"] * n_pages):
                removed.append(b)
                continue
        keep.append(b)
    return keep, removed


def parse_pdf(path: str | Path, file_hash: str | None = None, use_cache: bool = True) -> ParsedDoc:
    path = Path(path)
    file_hash = file_hash or docling_cache.file_sha256(path)
    doc, info = docling_cache.convert(path, file_hash, use_cache=use_cache)
    blocks, sizes = docling_blocks(doc)
    n_pages = doc.num_pages() if callable(getattr(doc, "num_pages", None)) else len(sizes)
    furniture = furniture_blocks(doc, sizes)
    blocks, furniture, restored = restore_unique_furniture(blocks, furniture)
    blocks, removed = remove_headers_footers(blocks, sizes, n_pages)
    info = {**info, "n_blocks": len(blocks), "n_removed_header_footer": len(removed), "n_restored_furniture": restored}
    return ParsedDoc(path, file_hash, n_pages, sizes, blocks, info, removed, furniture)


_HEADING_LIKE = re.compile(r"^(?:\d{1,2}[A-Z]?(?:\.\d{1,2})*\.?|[IVX]{1,4}\.|GR\d{1,2}(?:\.\d{1,2})*|CH[ƯU]ƠNG\s+\d+)\s",
                           re.IGNORECASE)


def restore_unique_furniture(blocks: list[Block], furniture: list[Block]):
    """Docling đôi khi gán nhầm heading cuối/đầu trang vào header/footer. Khối furniture có chữ, không lặp ở
    trang nào khác → trả lại thân tài liệu (chèn theo trang + y)."""
    pages_by_key: dict[str, set[int]] = defaultdict(set)
    for f in furniture:
        pages_by_key[_DIGITS.sub("#", f.text.lower()).strip()].add(f.page)
    keep, restored = [], 0
    out = list(blocks)
    for f in furniture:
        k = _DIGITS.sub("#", f.text.lower()).strip()
        if (len(pages_by_key[k]) == 1 and sum(c.isalpha() for c in f.text) >= 6 and len(f.text) < 200
                and _HEADING_LIKE.match(f.text)):
            nb = Block("heading", f.text, f.page, f.bbox,
                       label="restored_" + f.label, meta={"restored_furniture": True})
            pos = next((i for i, x in enumerate(out) if x.page > f.page or (x.page == f.page and x.bbox[1] > f.bbox[1])),
                       len(out))
            out.insert(pos, nb)
            restored += 1
        else:
            keep.append(f)
    return out, keep, restored


# ---------------------------------------------------------------- text layer (pypdfium2)

class TextLayer:
    """Đọc lại text theo vùng từ text layer PDF (dùng khi tách cột song ngữ / sửa ô bảng)."""

    def __init__(self, path: str | Path):
        import pypdfium2 as pdfium

        self._pdf = pdfium.PdfDocument(str(path))
        self._tp: dict[int, Any] = {}
        self._chars: dict[int, list] = {}
        self._h: dict[int, float] = {}

    def page_height(self, page: int) -> float:
        if page not in self._h:
            self._h[page] = self._pdf[page - 1].get_size()[1]
        return self._h[page]

    def text(self, page: int, bbox: tuple[float, float, float, float], pad: float = 0.5) -> str:
        """Text các ký tự có tâm nằm trong bbox (theo thứ tự text layer). Không dùng get_text_bounded
        (PDFium trả cả chữ ngoài vùng với một số file)."""
        l, t, r, b = bbox
        out: list[str] = []
        skipped = False
        for ch, (cl, ct, cr, cb) in self.chars(page):
            cx, cy = (cl + cr) / 2, (ct + cb) / 2
            inside = l - pad <= cx <= r + pad and t - pad <= cy <= b + pad and ch not in "\r\n"
            if not inside:
                skipped = skipped or bool(out)
                continue
            if skipped and out[-1] != " ":
                out.append(" ")
            skipped = False
            out.append(ch)
        return normalize_text("".join(out))

    def textpage(self, page: int):
        if page not in self._tp:
            self._tp[page] = self._pdf[page - 1].get_textpage()
        return self._tp[page]

    def chars(self, page: int) -> list[tuple[str, tuple[float, float, float, float]]]:
        """[(ký tự, bbox top-left)] của trang (cache)."""
        if page in self._chars:
            return self._chars[page]
        tp, h = self.textpage(page), self.page_height(page)
        out = []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            l, b, r, t = tp.get_charbox(i)
            out.append((ch, (l, h - t, r, h - b)))
        self._chars[page] = out
        return out

    def line_boxes(self, page: int) -> list[tuple[str, tuple[float, float, float, float]]]:
        """Các dòng text layer kèm bbox (tách theo ký tự xuống dòng)."""
        lines, cur, boxes = [], [], []
        for ch, bb in self.chars(page) + [("\n", (0, 0, 0, 0))]:
            if ch in "\r\n":
                txt = normalize_text("".join(cur))
                if txt and boxes:
                    lines.append((txt, (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                        max(b[2] for b in boxes), max(b[3] for b in boxes))))
                cur, boxes = [], []
                continue
            cur.append(ch)
            if ch.strip() and bb[2] > bb[0]:
                boxes.append(bb)
        return lines

    def page_lines(self, page: int) -> list[str]:
        if page not in self._tp:
            self._tp[page] = self._pdf[page - 1].get_textpage()
        return [normalize_text(x) for x in self._tp[page].get_text_range().replace("\r", "").split("\n") if x.strip()]
