"""Bước 4a – Nhận diện heading theo profile (config/headings.yaml) → cây mục (Section) + bảng (TableSec).

- Ứng viên heading: khối có nhãn Docling trong `labels` (mặc định section_header/title) hoặc đã được ghép cặp
  heading song ngữ, khớp regex của 1 cấp.
- Kiểm tra số mục tăng dần (không lùi, không nhảy cấp vô lý) → ứng viên sai = text thường (+ warning).
- Trang mục lục (MỤC LỤC / Table of Contents / NỘI DUNG) không sinh heading.
- section_key theo shared.keys.section_key; scope theo cấp gốc khi số mục đánh lại (profile › scope).
- Bảng: có caption "Bảng x.y" → table_x_y; không số → table_<số mục>_<n> (cùng scope với mục chứa nó).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingestion.parse import Block
from shared.keys import section_key

_TOC_TITLE = re.compile(r"^(?:MỤC LỤC|Table of Contents|NỘI DUNG|CONTENTS)\b", re.IGNORECASE)
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
PREAMBLE_KEY = "sec_0"


def roman_to_int(s: str) -> int:
    s, total, prev = s.upper(), 0, 0
    for ch in reversed(s):
        v = _ROMAN.get(ch, 0)
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


@dataclass
class Section:
    key: str
    kind: str
    number: str | None
    title: str | None
    level: int
    parent_key: str | None
    heading: Block | None = None
    blocks: list[Block] = field(default_factory=list)
    scope: str | None = None

    @property
    def display(self) -> str:
        return " ".join(x for x in (self.heading.text if self.heading else None,) if x) or (self.title or "")


@dataclass
class TableSec:
    key: str
    number: str | None
    caption: str | None
    section_key: str
    block: Block


@dataclass
class Structure:
    sections: list[Section]
    tables: list[TableSec]
    warnings: list[str]
    rejected: list[str]
    scoped: bool


@dataclass
class _Cand:
    idx: int
    level: dict
    num: str
    title: str


def _toc_pages(blocks: list[Block]) -> set[int]:
    pages = set()
    for b in blocks:
        if b.kind == "toc" or (b.kind in ("heading", "text") and len(b.text) < 40 and _TOC_TITLE.match(b.text)):
            pages.add(b.page)
    return pages


_SECOND_NUM = re.compile(r"\s(?=(?:\d{1,2}[A-Z]?|GR\d{1,2})\.\d{1,2}(?:\.\d{1,2})*\.?\s+[A-ZÀ-ỸĐ])")
_FIRST_NUM = re.compile(r"^(?:\d{1,2}[A-Z]?|GR\d{1,2}|[IVX]{1,4})(?:\.\d{1,2})*\.?\s")


def split_merged_headings(blocks: list[Block]) -> list[Block]:
    """'6. VAN 6.1. Tổng quan' (Docling gộp 2 heading 2 dòng) → 2 khối heading."""
    out = []
    for b in blocks:
        if b.kind == "heading" and len(b.text) < 200 and _FIRST_NUM.match(b.text):
            m = _SECOND_NUM.search(b.text, 3)
            if m:
                l, t, r, bt = b.bbox
                mid = (t + bt) / 2
                out.append(Block(b.kind, b.text[: m.start()].strip(), b.page, (l, t, r, mid), b.label, b.lang,
                                 meta={**b.meta, "split_heading": 1}))
                out.append(Block(b.kind, b.text[m.end():].strip(), b.page, (l, mid, r, bt), b.label, b.lang,
                                 meta={**b.meta, "split_heading": 2}))
                continue
        out.append(b)
    return out


def find_candidates(blocks: list[Block], prof: dict) -> list[_Cand]:
    toc = _toc_pages(blocks)
    toc_line = re.compile(prof.get("toc_line_pattern", r"$^"))
    levels = []
    for lv in prof["levels"]:
        flags = re.IGNORECASE if "i" in str(lv.get("flags", "")) else 0
        levels.append({**lv, "_re": re.compile(lv["pattern"], flags),
                       "_labels": set(lv.get("labels", ["section_header", "title"]))})
    out = []
    for i, b in enumerate(blocks):
        if b.kind in ("table", "picture", "toc") or b.page in toc or len(b.text) > 250 or "caption_of" in b.meta:
            continue
        for lv in levels:
            if not (b.kind == "heading" or b.label in lv["_labels"]):
                continue
            m = lv["_re"].match(b.text)
            if not m:
                continue
            if toc_line.search(b.text) and re.search(r"\.{4,}", b.text):
                break  # dòng mục lục lọt ra ngoài trang mục lục
            out.append(_Cand(i, lv, m.group("num").strip().rstrip("."), (m.groupdict().get("title") or "").strip()))
            break
    return out


def _dec(num: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in num.split("."))


class _Numbering:
    """Kiểm tra số mục trong 1 cấp gốc: không lùi; cha phải nằm trên đường dẫn hiện tại."""

    def __init__(self):
        self.cur: tuple | None = None
        self.last_child: dict[tuple, object] = {}
        self.letters: list[str] = []

    def accept_decimal(self, n: tuple) -> str | None:
        """→ 'ok' | 'dup' | None (từ chối)."""
        parent = n[:-1]
        if self.cur is not None and parent and self.cur[: len(parent)] != parent:
            # cha không nằm trên đường dẫn hiện tại – cho phép nếu cha "ngầm" là anh em kế tiếp ở cấp trên
            pp = parent[:-1]
            if self.cur[: len(pp)] != pp:
                return None
            last = self.last_child.get(pp)
            if last is not None and _gt(last, parent[-1]):
                return None
        last = self.last_child.get(parent)
        if last is not None:
            if last == n[-1]:
                return "dup"
            if _gt(last, n[-1]):
                return None
        if self.cur is not None and len(n) > len(self.cur) + 1:
            return None  # nhảy cấp (5.7 → 5.7.1.1)
        return "ok"

    def commit_decimal(self, n: tuple) -> None:
        self.cur = n
        for k in range(1, len(n)):  # cha "ngầm" (VD Spec: 5.1 mà không có heading "5")
            self.last_child.setdefault(n[: k - 1], n[k - 1])
        self.last_child[n[:-1]] = n[-1]
        # xoá thông tin con của các nhánh khác
        for key in [k for k in self.last_child if len(k) >= len(n) and k[: len(n)] != n]:
            del self.last_child[key]
        self.letters = []

    def accept_letter(self, ch: str) -> list[str] | None:
        """→ đường dẫn chữ cái mới (['g'] hoặc ['g','a']) | None."""
        st = self.letters
        if self.cur is None:
            return None
        if not st:
            return [ch]
        if len(st) == 1:
            if ch == "a":
                return [st[0], "a"]
            return [ch] if ch > st[0] else None
        top, sub = st
        if ch == "a":
            return [top, "a"]
        if ch > sub and ord(ch) - ord(sub) <= 2:
            return [top, ch]
        if ch > top:
            return [ch]
        return None


def _gt(a, b) -> bool:
    try:
        return a > b
    except TypeError:
        return str(a) > str(b)


def build_structure(blocks: list[Block], prof: dict, chapters: list[dict] | None = None) -> Structure:
    """Dựng cây mục cho 1 ngôn ngữ. `chapters`: [{num,title,page}] (Westin – từ mục lục)."""
    warnings: list[str] = []
    rejected: list[str] = []
    blocks = split_merged_headings(blocks)
    cands = {c.idx: c for c in find_candidates(blocks, prof)}
    scope_mode = prof.get("scope", "auto")

    # ---- lượt 1: chấp nhận heading
    accepted: dict[int, dict] = {}     # idx → {kind, num, title, level, root}
    chap_iter = iter(sorted(chapters or [], key=lambda c: c["page"]))
    next_chap = next(chap_iter, None)
    chapter_at: dict[int, dict] = {}   # idx block → chương bắt đầu trước khối này
    numbering = _Numbering()
    root_num: str | None = None
    roots_seen: list = []
    for i, b in enumerate(blocks):
        while next_chap and b.page >= next_chap["page"]:
            chapter_at.setdefault(i, next_chap)
            root_num = next_chap["num"]
            numbering = _Numbering()
            next_chap = next(chap_iter, None)
        c = cands.get(i)
        if not c:
            continue
        lv = c.level
        if lv.get("root"):
            val = roman_to_int(c.num) if c.level["kind"] == "part" else (int(c.num) if c.num.isdigit() else c.num)
            if roots_seen and not _gt(val, roots_seen[-1]):
                rejected.append(f"tr.{b.page} {b.text[:50]!r} (cấp gốc lùi)")
                continue
            roots_seen.append(val)
            root_num = c.num
            numbering = _Numbering()
            accepted[i] = {"kind": lv["kind"], "num": c.num, "title": c.title, "root": True}
            continue
        if lv.get("letter"):
            path = numbering.accept_letter(c.num.lower())
            if path is None:
                rejected.append(f"tr.{b.page} {b.text[:50]!r} (chữ cái sai thứ tự)")
                continue
            numbering.letters = path
            base = ".".join(str(x) for x in numbering.cur)
            accepted[i] = {"kind": lv["kind"], "num": base + "." + ".".join(path), "title": c.title,
                           "display": c.num, "depth": len(numbering.cur) + len(path)}
            continue
        n = _dec(c.num)
        if chapters:  # số mục phải thuộc chương hiện tại: 9.7 trong chương 9
            if root_num is None or str(n[0]).upper() != str(root_num).upper():
                rejected.append(f"tr.{b.page} {b.text[:50]!r} (không thuộc chương {root_num})")
                continue
            rel = n[1:]
        else:
            rel = n
        res = numbering.accept_decimal(rel)
        if res is None:
            rejected.append(f"tr.{b.page} {b.text[:50]!r} (số mục lùi/nhảy cấp)")
            continue
        numbering.commit_decimal(rel)
        accepted[i] = {"kind": lv["kind"], "num": c.num, "title": c.title, "depth": len(rel), "dup": res == "dup"}

    # ---- scope
    has_roots = any(a.get("root") for a in accepted.values()) or bool(chapters)
    if scope_mode == "always":
        scoped = has_roots
    elif scope_mode == "never" or chapters:
        scoped = False
    else:
        roots_of: dict[str, set] = {}
        cur_root = None
        for i in sorted(accepted):
            a = accepted[i]
            if a.get("root"):
                cur_root = a["num"]
                continue
            roots_of.setdefault(a["num"], set()).add(cur_root)
        scoped = has_roots and any(len(v) > 1 for v in roots_of.values())

    # ---- lượt 2: dựng Section
    sections: list[Section] = []
    tables: list[TableSec] = []
    used_keys: set[str] = set()

    def uniq(key: str) -> str:
        if key not in used_keys:
            used_keys.add(key)
            return key
        k = 2
        while f"{key}_dup{k}" in used_keys:
            k += 1
        warnings.append(f"section_key trùng: {key} → {key}_dup{k}")
        used_keys.add(f"{key}_dup{k}")
        return f"{key}_dup{k}"

    pre = Section(uniq(PREAMBLE_KEY), "sec", None, None, 0, None)
    sections.append(pre)
    stack: list[Section] = []          # đường dẫn hiện tại (theo level)
    root: Section | None = None
    cur = pre
    table_count: dict[str, int] = {}

    def open_section(sec: Section) -> None:
        nonlocal cur
        while stack and stack[-1].level >= sec.level:
            stack.pop()
        sec.parent_key = stack[-1].key if stack else None
        stack.append(sec)
        sections.append(sec)
        cur = sec

    for i, b in enumerate(blocks):
        if i in chapter_at:
            ch = chapter_at[i]
            heading = Block("heading", f"{ch['num']} {ch['title']}", b.page, b.bbox, label="toc_chapter", lang=b.lang,
                            meta={"from_toc": True})
            stack.clear()
            sec = Section(uniq(section_key(ch["num"], "sec")), "sec", ch["num"], ch["title"], 0, None, heading)
            open_section(sec)
            root = sec
        a = accepted.get(i)
        if a:
            if a.get("root"):
                stack.clear()
                sec = Section(uniq(section_key(a["num"], a["kind"])), a["kind"], a["num"], a["title"], 0, None, b)
                open_section(sec)
                root = sec
                continue
            scope = root.key if (scoped and root) else None
            key = uniq(section_key(a["num"], a["kind"], scope=scope))
            level = (1 if has_roots else 0) + a["depth"]
            sec = Section(key, a["kind"], a.get("display_full", a["num"]), a["title"], level, None, b, scope=scope)
            open_section(sec)
            continue
        if b.kind == "table":
            if b.number:
                key = uniq(section_key(b.number, "table"))
            else:
                n = table_count[cur.key] = table_count.get(cur.key, 0) + 1
                key = uniq(_unnumbered_table_key(cur, n))
            tables.append(TableSec(key, b.number, b.meta.get("caption"), cur.key, b))
            continue
        if b.kind == "picture" or "caption_of" in b.meta:
            continue  # caption "Bảng x.y" thuộc parent TABLE
        cur.blocks.append(b)
    if rejected:
        warnings.append(f"{len(rejected)} ứng viên heading bị loại (số mục sai thứ tự): " + "; ".join(rejected[:8]))
    return Structure(sections, tables, warnings, rejected, scoped)


def _unnumbered_table_key(cur: Section, n: int) -> str:
    """Bảng không đánh số: table_<số mục>_<n>; mục gốc (chương/phần) → <gốc>__table_0_<n>; mở đầu → table_0_<n>."""
    if cur.key == PREAMBLE_KEY or not cur.number:
        return section_key(f"0.{n}", "table")
    if cur.level == 0 and cur.kind != "sec":
        return section_key(f"0.{n}", "table", scope=cur.key)
    if cur.level == 0:
        return section_key(f"{cur.number}.0.{n}", "table")
    return section_key(f"{cur.number}.{n}", "table", scope=cur.scope)


def toc_chapters(lines_by_page: dict[int, list[str]], cfg: dict, n_pages: int = 10**6) -> list[dict]:
    """Mục lục chương (Westin): 'GR1 Overview & Project Administration 4' → {num, title, page}.
    Trang bắt đầu phải tăng dần và ≤ số trang (loại dòng footer kiểu '2 Westin … December 2025')."""
    pat = re.compile(cfg["pattern"])
    out, seen = [], set()
    for page in sorted(lines_by_page):
        for ln in lines_by_page[page]:
            m = pat.match(ln.strip())
            if not m or int(m.group("page")) > n_pages or (out and int(m.group("page")) < out[-1]["page"]):
                continue
            if m.group("num").upper() not in seen:
                seen.add(m.group("num").upper())
                out.append({"num": m.group("num"), "title": m.group("title").strip(), "page": int(m.group("page"))})
        if out:
            break
    return out


def _alnum(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def recover_textlayer_headings(blocks: list[Block], layer, prof: dict, n_pages: int, lang: str) -> int:
    """Heading có trong text layer nhưng Docling bỏ sót (nằm trong vùng hình, nhãn caption…) → chèn khối heading.
    Chỉ nhận dòng khớp regex 1 cấp heading, không trùng text khối nào cùng trang. Còn phải qua kiểm tra số mục."""
    levels = []
    for lv in prof["levels"]:
        flags = re.IGNORECASE if "i" in str(lv.get("flags", "")) else 0
        levels.append(re.compile(lv["pattern"], flags))
    toc = _toc_pages(blocks)
    by_page: dict[int, list[str]] = {}
    for b in blocks:
        if b.kind == "table":
            by_page.setdefault(b.page, []).append(" ".join(" ".join(r) for r in b.rows or []))
        else:
            by_page.setdefault(b.page, []).append(b.text)
    added = 0
    for page in range(1, n_pages + 1):
        if page in toc:
            continue
        texts = by_page.get(page - 1, []) + by_page.get(page, []) + by_page.get(page + 1, [])
        joined = _alnum(" ".join(texts))
        for txt, bb in layer.line_boxes(page):
            if len(txt) > 160 or not any(p.match(txt) for p in levels):
                continue
            if re.search(r"\.{4,}|\s\d{1,4}$", txt):
                continue  # dòng mục lục
            if _alnum(txt) in joined:
                continue
            nb = Block("heading", txt, page, bb, label="textlayer_heading", lang=lang, meta={"recovered": True})
            pos = next((i for i, x in enumerate(blocks) if x.page > page or (x.page == page and x.bbox[1] > bb[1])),
                       len(blocks))
            blocks.insert(pos, nb)
            by_page.setdefault(page, []).append(txt)
            added += 1
    return added


def reorder_headings(blocks: list[Block]) -> list[Block]:
    """Heading bị Docling xếp cuối trang dù nằm đầu trang → chuyển lên trước khối đầu tiên nằm dưới nó."""
    out = list(blocks)
    i = 0
    while i < len(out):
        h = out[i]
        if h.kind == "heading":
            j = next((k for k in range(i) if out[k].page == h.page and out[k].kind != "picture"
                      and out[k].bbox[1] > h.bbox[3] and out[k].lang == h.lang
                      and abs(out[k].bbox[0] - h.bbox[0]) < 400), None)
            if j is not None and all(out[k].page == h.page for k in range(j, i)):
                out.insert(j, out.pop(i))
        i += 1
    return out
