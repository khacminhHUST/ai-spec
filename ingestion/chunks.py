"""Bước 4b + 5 – Parent chunks (mục / bảng) và Child chunks (text / dòng bảng) + kiểm tra toàn vẹn.

Parent SECTION: content_md = dòng heading + nội dung trực tiếp của mục (không gồm mục con, không gồm bảng).
Parent TABLE  : content_md = caption (nếu có) + header + các dòng; parent_section_key = mục chứa bảng.
Child TEXT    : đơn vị = đoạn (mục vừa) hoặc câu (mục dài / có đoạn quá dài, ¶ = đầu đoạn) → LLM nhóm → ghép text gốc.
Child TABLE   : nhóm dòng theo dòng phân nhóm (General, Hotel Room…) hoặc gộp tới max; luôn kèm tên bảng + header + nhóm.
Toàn vẹn      : nối raw_text các child (bỏ khoảng trắng) == nội dung parent (bỏ heading/header lặp).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ingestion import chunk_llm
from ingestion.metadata import DocMeta
from ingestion.parse import Block
from ingestion.sentences import mask, split_sentences
from ingestion.structure import PREAMBLE_KEY, Section, Structure, TableSec
from ingestion.textutil import chunk_cfg, squash
from shared.keys import extract_references, make_child_id, make_parent_id, section_key
from shared.schema import ChildChunk, ParentChunk
from shared.tokens import count_tokens


@dataclass
class Unit:
    text: str
    page: int
    para_start: bool
    page_end: int | None = None

    @property
    def last_page(self) -> int:
        return self.page_end or self.page


@dataclass
class ParentDraft:
    parent: ParentChunk
    display: str                       # "5.7 DÂY VÀ CÁP ĐIỆN" – dùng cho breadcrumb child
    body: str                          # phần so sánh toàn vẹn
    paragraphs: list[Unit] = field(default_factory=list)
    table: dict | None = None          # {caption, header_rows, rows, row_pages}
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChildDraft:
    raw: str
    topic: str | None
    page_start: int | None
    page_end: int | None
    prefix: str = ""                   # header/tên bảng/nhóm lặp lại (không thuộc raw)


# ---------------------------------------------------------------- render

def _cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def md_row(row: list[str]) -> str:
    return "| " + " | ".join(_cell(c) for c in row) + " |"


def table_header_md(header_rows: list[list[str]], ncol: int) -> str:
    if not header_rows:
        return ""
    return "\n".join(md_row(r) for r in header_rows) + "\n" + "|" + "---|" * ncol


def is_group_row(row: list[str]) -> bool:
    vals = [c.strip() for c in row if c.strip()]
    if len(row) < 2 or not vals or len(vals[0]) > 80:
        return False
    uniq = set(vals)
    return len(uniq) == 1 and (len(vals) == len(row) or (row[0].strip() and len(vals) == 1)) and not re.fullmatch(
        r"[\d\s.,\-–]+", vals[0])


def heading_line(sec: Section) -> str:
    if not sec.heading:
        return ""
    return "#" * min(6, sec.level + 1) + " " + sec.heading.text


# ---------------------------------------------------------------- parents

def _display(sec: Section) -> str:
    if sec.heading:
        return sec.heading.text
    return "Mở đầu / trang bìa" if sec.key == PREAMBLE_KEY else (sec.title or sec.key)


def _path(sec: Section, by_key: dict[str, Section]) -> list[str]:
    out, cur, guard = [], sec, 0
    while cur and guard < 20:
        out.append(_display(cur))
        cur = by_key.get(cur.parent_key) if cur.parent_key else None
        guard += 1
    return list(reversed(out))


def _short_path(parts: list[str], limit: int = 160) -> str:
    s = " > ".join(parts)
    while len(s) > limit and len(parts) > 2:
        parts = [parts[0], "…", *parts[2:]] if parts[1] != "…" else [parts[0], "…", *parts[3:]]
        s = " > ".join(parts)
    return s


def split_large_sections(st: Structure, prof: dict, lang: str, warnings: list[str]) -> None:
    """Mục có nội dung trực tiếp > max_parent_tokens → cắt tại heading cấp con (split_patterns), rồi theo khối."""
    max_parent = chunk_cfg()["max_parent_tokens"]
    pats = [re.compile(p) for p in prof.get("split_patterns", [])]
    out: list[Section] = []
    used = {s.key for s in st.sections} | {t.key for t in st.tables}
    for sec in st.sections:
        out.append(sec)
        body = "\n\n".join(b.text for b in sec.blocks if b.kind != "table")
        if count_tokens(body, lang) <= max_parent:
            continue
        parts: list[tuple[str | None, list[Block]]] = [(None, [])]
        for b in sec.blocks:
            m = next((p.match(b.text) for p in pats if b.kind != "table" and p.match(b.text)), None)
            if m:
                parts.append((m.group("num") if "num" in m.groupdict() else str(len(parts)), [b]))
            else:
                parts[-1][1].append(b)
        final: list[tuple[str | None, list[Block]]] = []
        for label, blocks in parts:
            # phần còn vượt → chia tham lam theo khối
            chunks: list[list[Block]] = []
            chunk, tok = [], 0
            for b in blocks:
                t = count_tokens(b.text, lang)
                if chunk and tok + t > max_parent:
                    chunks.append(chunk)
                    chunk, tok = [], 0
                chunk.append(b)
                tok += t
            if chunk:
                chunks.append(chunk)
            for j, ch in enumerate(chunks):
                lab = (None if j == 0 else f"p{j + 1}") if label is None else (label if j == 0 else f"{label}p{j + 1}")
                final.append((lab, ch))
        sec.blocks = next((bl for lab, bl in final if lab is None), [])
        subs = [(lab, bl) for lab, bl in final if lab is not None]
        for label, blocks in subs:
            num = f"{sec.number}.{label}" if sec.number else label
            key = section_key(num, "sec", scope=sec.scope)
            k = 2
            while key in used:
                key = section_key(f"{num}_{k}", "sec", scope=sec.scope)
                k += 1
            used.add(key)
            title = re.split(r"[:.]\s", blocks[0].text, maxsplit=1)[0][:80] if blocks else None
            sub = Section(key, "sec", num, title, sec.level + 1, sec.key, None, blocks, sec.scope)
            out.append(sub)
        warnings.append(f"{lang}: mục {sec.key} vượt max_parent_tokens → cắt thêm {len(subs)} phần con")
    st.sections = out


def build_parents(st: Structure, meta: DocMeta, lang: str) -> list[ParentDraft]:
    by_key = {s.key: s for s in st.sections}
    head = [meta.project_label, meta.stream or "?", "/".join(meta.disciplines) or "?"]
    drafts: list[ParentDraft] = []
    for sec in st.sections:
        paras = [Unit(b.text, b.page, True, b.last_page) for b in sec.blocks if b.kind != "table" and b.text.strip()]
        body = "\n\n".join(u.text for u in paras)
        hl = heading_line(sec)
        content = "\n\n".join(x for x in (hl, body) if x) or (sec.title or sec.key)
        pages = [p for b in sec.blocks if b.kind != "table" for p in (b.page, b.last_page)] + (
            [sec.heading.page] if sec.heading else [])
        path = _path(sec, by_key)
        refs_text = (sec.heading.text + "\n" if sec.heading else "") + body
        p = ParentChunk(
            parent_id=make_parent_id(meta.doc_id, lang, sec.key), doc_id=meta.doc_id, section_key=sec.key,
            parent_section_key=sec.parent_key, lang=lang, type="SECTION", number=sec.number, title=sec.title,
            breadcrumb=" | ".join(head + [" > ".join(path)]), content_md=content,
            page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
            referenced_ids=[r for r in extract_references(refs_text) if r != sec.key],
            token_count=count_tokens(content, lang),
        )
        drafts.append(ParentDraft(p, _short_path(path), body, paras))
    for t in st.tables:
        drafts.append(_table_parent(t, by_key, meta, lang, head))
    return drafts


def _table_parent(t: TableSec, by_key: dict[str, Section], meta: DocMeta, lang: str, head: list[str]) -> ParentDraft:
    b = t.block
    rows = [list(r) for r in (b.rows or [])]
    ncol = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    nh = min(b.header_rows, len(rows))
    header, data = rows[:nh], rows[nh:]
    row_pages = b.meta.get("row_pages") or [b.page] * len(rows)
    row_pages = (row_pages + [b.last_page] * len(rows))[: len(rows)]
    sec = by_key.get(t.section_key)
    sec_path = _path(sec, by_key) if sec else []
    caption = t.caption
    name = caption or (f"Bảng trong mục {_display(sec)}" if lang == "vi" else f"Table in {_display(sec)}") if sec else (
        caption or t.key)
    hdr_md = table_header_md(header, ncol) if header else ""
    rows_md = "\n".join(md_row(r) for r in data)
    content = "\n\n".join(x for x in (caption, (hdr_md + "\n" + rows_md) if hdr_md else rows_md) if x)
    refs = extract_references((caption or "") + "\n" + "\n".join(" ".join(r) for r in rows))
    p = ParentChunk(
        parent_id=make_parent_id(meta.doc_id, lang, t.key), doc_id=meta.doc_id, section_key=t.key,
        parent_section_key=t.section_key, lang=lang, type="TABLE", number=t.number, title=caption,
        breadcrumb=" | ".join(head + [" > ".join(sec_path + [name])]), content_md=content,
        page_start=b.page, page_end=b.last_page, referenced_ids=[r for r in refs if r != t.key],
        token_count=count_tokens(content, lang),
    )
    return ParentDraft(p, _short_path(sec_path[-2:] + [name]), rows_md,
                       table={"caption": caption, "name": name, "header": header, "rows": data,
                              "row_pages": row_pages[nh:], "ncol": ncol})


# ---------------------------------------------------------------- children: text

def hard_split(text: str, lang: str, budget: int) -> list[str]:
    """Đơn vị vẫn quá dài (câu không dấu chấm…) → cắt tại khoảng trắng không nằm trong mẫu bị che."""
    masked = mask(text)
    # khoảng trắng bên trong mã tiêu chuẩn (TCVN 5687:2024) – che thêm
    protected = set()
    for m in re.finditer(r"\b[A-Z]{2,}(?:\s?[A-Z]{1,3})?\s\d[\w.:\-/]*", text):
        protected.update(range(m.start(), m.end()))
    spaces = [m.start() for m in re.finditer(r"\s+", masked) if m.start() not in protected]
    pieces, start = [], 0
    while start < len(text):
        if count_tokens(text[start:], lang) <= budget:
            pieces.append(text[start:])
            break
        lo, hi, best = start + 1, len(text), None
        cand = [s for s in spaces if s > start]
        if not cand:
            pieces.append(text[start:])
            break
        # tìm điểm cắt xa nhất mà đoạn ≤ budget (nhị phân trên danh sách khoảng trắng)
        a, b = 0, len(cand) - 1
        while a <= b:
            mid = (a + b) // 2
            if count_tokens(text[start:cand[mid]], lang) <= budget:
                best = cand[mid]
                a = mid + 1
            else:
                b = mid - 1
        if best is None:
            best = cand[0]
        end = best
        while end < len(text) and text[end].isspace():
            end += 1
        pieces.append(text[start:end])
        start = end
    return [p for p in pieces if p]


def text_units(d: ParentDraft, lang: str, budget: int) -> tuple[list[Unit], str]:
    cfg = chunk_cfg()
    paras = d.paragraphs
    toks = [count_tokens(u.text, lang) for u in paras]
    total = count_tokens(d.body, lang)
    if total <= budget:
        return paras, "single"
    if total > cfg["long_section_tokens"] or max(toks) > budget:
        units: list[Unit] = []
        for u in paras:
            sents = split_sentences(u.text, lang)
            first = True
            for s in sents:
                for piece in (hard_split(s, lang, budget) if count_tokens(s, lang) > budget else [s]):
                    units.append(Unit(piece, u.page, first, u.page_end))
                    first = False
        return units, "sentence"
    return paras, "paragraph"


def join_units(units: list[Unit]) -> str:
    out = []
    for i, u in enumerate(units):
        if i and u.para_start:
            out.append("\n\n")
        out.append(u.text)
    return "".join(out).strip()


# ---------------------------------------------------------------- children: bảng

def table_children(d: ParentDraft, lang: str, max_tokens: int, bc_tokens: int, warnings: list[str]) -> list[ChildDraft]:
    t = d.table
    ncol = t["ncol"]
    hdr = table_header_md(t["header"], ncol)
    rows, pages = t["rows"], t["row_pages"]
    if not rows:
        return []
    name = t["name"]

    def prefix(group: str | None) -> str:
        return "\n".join(x for x in (name, hdr, (f"[{group}]" if group else None)) if x)

    whole = prefix(None) + "\n" + "\n".join(md_row(r) for r in rows)
    if count_tokens(whole, lang) + bc_tokens <= max_tokens:
        return [ChildDraft("\n".join(md_row(r) for r in rows), None, min(pages), max(pages), prefix(None))]
    # chia theo dòng phân nhóm
    segs: list[tuple[str | None, list[int]]] = []
    for i, r in enumerate(rows):
        if is_group_row(r):
            segs.append((next(c for c in r if c.strip()).strip(), [i]))
        elif segs:
            segs[-1][1].append(i)
        else:
            segs.append((None, [i]))
    out: list[ChildDraft] = []
    for group, idxs in segs:
        cur: list[int] = []
        cur_tok = 0
        first = True
        for i in idxs:
            line = md_row(rows[i])
            pre = prefix(None if first else group)
            base = count_tokens(pre, lang) + bc_tokens + 4
            lt = count_tokens(line, lang)
            if cur and base + cur_tok + lt > max_tokens:
                out.append(ChildDraft("\n".join(md_row(rows[k]) for k in cur), group,
                                      min(pages[k] for k in cur), max(pages[k] for k in cur),
                                      prefix(None if first else group)))
                first = False
                cur, cur_tok = [], 0
                base = count_tokens(prefix(group), lang) + bc_tokens + 4
            if not cur and base + lt > max_tokens:
                warnings.append(f"{d.parent.section_key}: dòng bảng đơn lẻ vượt max_child_tokens ({lt} token)")
            cur.append(i)
            cur_tok += lt
        if cur:
            out.append(ChildDraft("\n".join(md_row(rows[k]) for k in cur), group,
                                  min(pages[k] for k in cur), max(pages[k] for k in cur),
                                  prefix(None if first else group)))
    return out


# ---------------------------------------------------------------- tổng hợp

def breadcrumb(meta: DocMeta, display: str, topic: str | None) -> str:
    parts = [meta.project_label, meta.stream or "?", "/".join(meta.disciplines) or "?", display]
    if topic:
        parts.append(topic)
    return "[" + " | ".join(parts) + "]"


def build_children(drafts: list[ParentDraft], meta: DocMeta, lang: str, llm, *, workers: int = 8,
                   use_cache: bool = True) -> tuple[list[ChildChunk], dict]:
    cfg = chunk_cfg()
    max_t, min_t = cfg["max_child_tokens"], cfg["min_child_tokens"]
    stats = {"llm": 0, "llm_cache": 0, "fallback": 0, "single": 0, "fallback_reasons": [], "warnings": []}
    jobs: dict[int, tuple[list[Unit], int]] = {}
    plans: dict[int, list[ChildDraft]] = {}
    for i, d in enumerate(drafts):
        bc_tok = count_tokens(breadcrumb(meta, d.display, None), lang) + 16  # + chỗ cho topic
        if d.table is not None:
            plans[i] = table_children(d, lang, max_t, bc_tok, stats["warnings"])
            continue
        if not d.body.strip():
            plans[i] = []
            continue
        budget = max_t - bc_tok
        units, mode = text_units(d, lang, budget)
        if mode == "single":
            plans[i] = [ChildDraft(d.body.strip(), None, min(u.page for u in d.paragraphs),
                                   max(u.last_page for u in d.paragraphs))]
            stats["single"] += 1
        else:
            jobs[i] = (units, budget)

    def run(i: int):
        units, budget = jobs[i]
        toks = [count_tokens(u.text, lang) + 1 for u in units]  # +1: dấu xuống dòng/khoảng trắng khi ghép
        g = chunk_llm.group_units([u.text for u in units], toks, [u.para_start for u in units], llm, budget, min_t,
                                  use_cache=use_cache)
        return i, units, g

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, units, g in ex.map(run, list(jobs)):
            stats[g.source] = stats.get(g.source, 0) + 1
            if g.reason:
                stats["fallback_reasons"].append(f"{drafts[i].parent.section_key}: {g.reason}")
            plans[i] = [ChildDraft(join_units(units[s:e + 1]), topic, min(u.page for u in units[s:e + 1]),
                                   max(u.last_page for u in units[s:e + 1])) for s, e, topic in g.groups]
    children: list[ChildChunk] = []
    for i, d in enumerate(drafts):
        p = d.parent
        for seq, cd in enumerate(x for x in plans.get(i, []) if x.raw.strip()):
            bc = breadcrumb(meta, d.display, cd.topic)
            text = "\n".join(x for x in (bc, cd.prefix, cd.raw) if x)
            children.append(ChildChunk(
                child_id=make_child_id(p.parent_id, seq), parent_id=p.parent_id, section_key=p.section_key, lang=lang,
                type="TABLE_ROWS" if d.table is not None else "TEXT", text=text, raw_text=cd.raw, topic=cd.topic,
                page_start=cd.page_start, page_end=cd.page_end, project_id=meta.project_id, building=meta.building,
                stream=meta.stream, disciplines=meta.disciplines, doc_id=meta.doc_id, revision=meta.revision,
                is_latest=True,
            ))
    return children, stats


# ---------------------------------------------------------------- kiểm tra

def integrity_check(draft: ParentDraft, children: list[ChildChunk]) -> bool:
    """Nối raw_text các child (bỏ khoảng trắng / ký hiệu bảng) == nội dung parent."""
    return squash("".join(c.raw_text for c in children)) == squash(draft.body)


def boundary_violations(children: list[ChildChunk]) -> list[str]:
    """Ranh giới giữa 2 child liên tiếp cùng parent không được nằm trong số thập phân / mã tiêu chuẩn."""
    from ingestion.sentences import _protect_re

    bad = []
    for a, b in zip(children, children[1:]):
        if a.parent_id != b.parent_id or a.type != "TEXT":
            continue
        left, right = a.raw_text[-40:], b.raw_text[:40]
        joined = left + " " + right
        cut = len(left)
        for m in _protect_re().finditer(joined):
            if m.start() < cut < m.end() - 1 and not joined[m.start():m.end()].endswith(" "):
                bad.append(f"{a.child_id}|{b.child_id}: {joined[max(0, m.start() - 5):m.end() + 5]!r}")
        if re.search(r"\d[.,]$", left.rstrip()) and re.match(r"\d", right) and not left.endswith((" ", "\n")):
            bad.append(f"{a.child_id}|{b.child_id}: số thập phân bị cắt")
    return bad
