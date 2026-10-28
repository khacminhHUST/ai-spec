"""Bước 3 – Tách song ngữ + hậu xử lý khối theo từng ngôn ngữ.

- Nhận diện song ngữ: tỷ lệ khối VN/EN theo cột trên từng trang.
- Tách theo cột: tâm bbox so với `split_x` (profile; Spec CPG: EN trái, VN phải). Kiểm tra lại bằng lingua → warning.
- Heading song ngữ: cặp heading cùng hàng – bên thiếu số mục được gắn số của bên kia (cả hai giữ số mục).
- Bảng (kể cả "bảng bố cục" Docling nhận nhầm do trang 2 cột): mỗi ô cắt theo cột ngôn ngữ, đọc lại text
  theo vùng từ text layer; dòng 1 ô → đoạn văn, ≥ 2 ô liền nhau → bảng thật.
- Khối full-width không có chữ → đưa vào cả hai.
Sau đó (mọi tài liệu): gộp khối cùng dòng, ghép bảng ngắt trang, gắn caption "Bảng x.y".
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from ingestion.parse import Block, Cell, ParsedDoc, TextLayer
from ingestion.textutil import detect_lang, has_letters, ingestion_cfg

_NUM_PREFIX = re.compile(r"^\s*((?:\d{1,2}[A-Z]?|GR\d{1,2})(?:\.\d{1,2}){0,3}\.?)\s+")
_BULLET = re.compile(r"^[•▪➢❖✓\-–*]\s")


def _cx(b) -> float:
    return (b[0] + b[2]) / 2


def _union(bbs):
    return (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))


# ---------------------------------------------------------------- nhận diện

def detect_languages(parsed: ParsedDoc, prof: dict) -> dict:
    """→ {bilingual, primary, split_x, left, right, ratio, pages_both}."""
    cfg = ingestion_cfg()["language"]
    counts: Counter = Counter()
    side_lang: dict[tuple[int, str], Counter] = {}
    bil = prof.get("bilingual") or {}
    for b in parsed.blocks:
        if b.kind not in ("text", "heading", "list") or len(b.text) < 30:
            continue
        lang = detect_lang(b.text)
        if not lang:
            continue
        counts[lang] += len(b.text)
        w = parsed.page_sizes.get(b.page, (595, 842))[0]
        side = "L" if _cx(b.bbox) < w * bil.get("split_x", 0.5) else "R"
        side_lang.setdefault((b.page, side), Counter())[lang] += 1
    total = sum(counts.values()) or 1
    primary = counts.most_common(1)[0][0] if counts else "vi"
    minor = 1 - counts[primary] / total
    pages = {p for p, _ in side_lang}
    both = 0
    for p in pages:
        left, right = side_lang.get((p, "L"), Counter()), side_lang.get((p, "R"), Counter())
        if left and right and left.most_common(1)[0][0] != right.most_common(1)[0][0]:
            both += 1
    pages_both = both / (len(pages) or 1)
    bilingual = minor >= cfg["bilingual_min_ratio"] and pages_both >= cfg["bilingual_min_pages"]
    res = {"bilingual": bilingual, "primary": primary, "ratio": round(minor, 3), "pages_both": round(pages_both, 3),
           "chars": dict(counts)}
    if bilingual:
        if bil:
            res.update(left=bil["left"], right=bil["right"], split_x=bil["split_x"])
        else:  # tự suy cột: ngôn ngữ đa số mỗi bên
            lc = sum((c for (p, s), c in side_lang.items() if s == "L"), Counter())
            res.update(left=lc.most_common(1)[0][0], split_x=0.5)
            res["right"] = "en" if res["left"] == "vi" else "vi"
    return res


# ---------------------------------------------------------------- tách bảng theo cột ngôn ngữ

def _x_gaps(layer: TextLayer, page: int, bbox) -> list[tuple[float, float]]:
    """Các khe trống theo trục x (không ký tự nào phủ) trong vùng bbox."""
    l, t, r, b = bbox
    spans = sorted((cl, cr) for ch, (cl, ct, cr, cb) in layer.chars(page)
                   if ch.strip() and cr > cl and l - 1 <= (cl + cr) / 2 <= r + 1 and t - 1 <= (ct + cb) / 2 <= b + 1)
    if not spans:
        return []
    gaps, cur = [], spans[0][1]
    for s0, s1 in spans[1:]:
        if s0 > cur + 1.5:
            gaps.append((cur, s0))
        cur = max(cur, s1)
    return gaps


def _gap_near(gaps: list[tuple[float, float]], x: float, tol: float = 25) -> float | None:
    cands = [g for g in gaps if g[0] - tol <= x <= g[1] + tol]
    if not cands:
        return None
    g = min(cands, key=lambda g: abs((g[0] + g[1]) / 2 - x))
    return (g[0] + g[1]) / 2


def _split_cell_by_gaps(layer: TextLayer, page: int, bbox, approx: list[float]) -> list[str] | None:
    """Cắt 1 ô thành nhiều ô tại khe trống theo trục x gần các mốc `approx`."""
    gaps = _x_gaps(layer, page, bbox)
    cuts = []
    for a in approx:
        c = _gap_near(gaps, a)
        if c is None:
            return None
        cuts.append(c)
    l, t, r, b = bbox
    xs = [l] + sorted(cuts) + [r]
    out = [layer.text(page, (xs[i], t, xs[i + 1], b)) for i in range(len(xs) - 1)]
    return out if all(out) else None


def split_table_by_side(blk: Block, layer: TextLayer, split: float, page_w: float, langs: tuple[str, str],
                        warnings: list[str]) -> dict[str, list[Block]]:
    """Bảng song ngữ → {lang: [Block]} (đoạn văn + bảng con theo từng cột ngôn ngữ)."""
    cells: list[Cell] = blk.cells or []
    sides = {langs[0]: (0.0, split), langs[1]: (split, page_w)}
    n_rows = max((c.r1 for c in cells), default=0)
    # đỉnh của hàng kế tiếp → chặn đáy vùng đọc lại (ô Docling đôi khi phình xuống hàng dưới)
    row_top = {}
    for c in cells:
        row_top[c.r0] = min(row_top.get(c.r0, 1e9), c.bbox[1])
    per_side: dict[str, dict[int, list[tuple[int, str, tuple]]]] = {lg: {} for lg in sides}
    for c in sorted(cells, key=lambda c: (c.r0, c.c0)):
        l, t, r, b = c.bbox
        nxt = row_top.get(c.r1)
        if nxt is not None and nxt > t:
            b = min(b, nxt - 0.5)
        for lg, (sl, sr) in sides.items():
            rl, rr = max(l, sl), min(r, sr)
            if rr - rl < 8:
                continue
            inside = l >= sl - 2 and r <= sr + 2 and b >= c.bbox[3] - 1
            text = c.text if inside else layer.text(blk.page, (rl, t, rr, b))
            if not text:
                continue
            row = per_side[lg].setdefault(c.r0, [])
            if row and row[-1][1] == text:
                continue
            row.append((c.c0, text, (rl, t, rr, b)))
    # sửa dòng header bị gộp 1 ô ở một bên trong khi bên kia có ≥ 2 ô
    l0 = {lg: min((x[2][0] for row in rows.values() for x in row), default=0) for lg, rows in per_side.items()}
    for r in range(n_rows):
        for lg, other in ((langs[0], langs[1]), (langs[1], langs[0])):
            mine, theirs = per_side[lg].get(r), per_side[other].get(r)
            if mine and theirs and len(mine) == 1 and len(theirs) >= 2:
                approx = [l0[lg] + (x[2][0] - l0[other]) for x in theirs[1:]]
                parts = _split_cell_by_gaps(layer, blk.page, mine[0][2], approx)
                if parts:
                    bb = mine[0][2]
                    per_side[lg][r] = [(i, p, bb) for i, p in enumerate(parts)]
    out: dict[str, list[Block]] = {}
    for lg, rows in per_side.items():
        res: list[Block] = []
        grid: list[tuple[int, list]] = []

        def flush():
            if not grid:
                return
            ncol = max(len(x) for _, x in grid)
            tb = Block("table", "", blk.page, _union([c[2] for _, x in grid for c in x]), label="table",
                       lang=lg, rows=[[c[1] for c in x] + [""] * (ncol - len(x)) for _, x in grid],
                       header_rows=1 if any(cc.header and cc.r0 == grid[0][0] for cc in cells) else 0,
                       meta={"from_bilingual_table": True})
            res.append(tb)
            grid.clear()

        for r in sorted(rows):
            row = rows[r]
            if len(row) >= 2:
                grid.append((r, row))
                continue
            flush()
            text = row[0][1]
            kind = "list" if _BULLET.match(text) else "text"
            res.append(Block(kind, text, blk.page, row[0][2], label="table_cell", lang=lg,
                             meta={"from_bilingual_table": True}))
        flush()
        out[lg] = res
    return out


# ---------------------------------------------------------------- tách khối

def split_languages(parsed: ParsedDoc, prof: dict, langinfo: dict, warnings: list[str]) -> dict[str, list[Block]]:
    if not langinfo["bilingual"]:
        lang = langinfo["primary"]
        return {lang: [replace(b, lang=lang) for b in parsed.blocks if b.kind not in ("picture",)]}
    left, right = langinfo["left"], langinfo["right"]
    layer = TextLayer(parsed.path)
    out: dict[str, list[Block]] = {left: [], right: []}
    mismatch = 0
    stats = Counter()
    columnar = _columnar_pages(parsed, langinfo["split_x"])
    for b in parsed.blocks:
        if b.kind == "picture":
            continue
        w = parsed.page_sizes.get(b.page, (595, 842))[0]
        split = w * langinfo["split_x"]
        if not columnar.get(b.page, True):
            # trang không chia cột (bìa, giới thiệu: dòng EN rồi dòng VN) → theo lingua
            lg = detect_lang(b.text) if b.kind != "table" else None
            targets = [lg] if lg else [left, right]
            for x in targets:
                out[x].append(replace(b, lang=x, meta={**b.meta, "non_columnar_page": True}))
            stats["non_columnar"] += 1
            continue
        if b.kind == "table":
            parts = split_table_by_side(b, layer, split, w, (left, right), warnings)
            for lg, bl in parts.items():
                out[lg].extend(bl)
            stats["tables"] += 1
            continue
        if b.kind == "toc" or not has_letters(b.text):
            out[left].append(replace(b, lang=left))
            out[right].append(replace(b, lang=right))
            continue
        # Đọc lại text theo vùng cột từ text layer: Docling đôi khi gán chữ cột bên kia vào khối này.
        # Khối vắt qua đường chia cột → cắt tại khe trống gần đường chia; không có khe → giữ nguyên, theo lingua.
        l, t, r, bt = b.bbox
        if l < split - 8 and r > split + 8:
            cut = _gap_near(_x_gaps(layer, b.page, b.bbox), split)
            if cut is None:
                text = layer.text(b.page, b.bbox) or b.text
                lg = detect_lang(text) or (left if _cx(b.bbox) < split else right)
                out[lg].append(replace(b, text=text, lang=lg, meta={**b.meta, "full_width": True}))
                continue
            regions = [(left, (l, t, cut, bt)), (right, (cut, t, r, bt))]
        else:
            regions = [(left if _cx(b.bbox) < split else right, b.bbox)]
        for side, region in regions:
            text = layer.text(b.page, region)
            if not text:
                continue
            if text != b.text:
                stats["reextracted"] += 1
            det = detect_lang(text)
            if det and det != side and len(text) >= 25:
                mismatch += 1
                if mismatch <= 30:
                    warnings.append(f"lệch cột/ngôn ngữ tr.{b.page}: cột {side}, lingua {det}: {text[:60]!r}")
            out[side].append(replace(b, text=text, bbox=region, lang=side, meta=dict(b.meta)))
    if mismatch > 30:
        warnings.append(f"… tổng {mismatch} khối lệch cột/ngôn ngữ")
    n_orphans = recover_orphans(parsed, layer, out, langinfo)
    stats["orphans"] = n_orphans
    langinfo["split_stats"] = dict(stats)
    pair_bilingual_headings(out[left], out[right], warnings)
    return out


def _columnar_pages(parsed: ParsedDoc, split_x: float) -> dict[int, bool]:
    """Trang chia 2 cột ngôn ngữ: ≥ 60% khối text (≥ 30 ký tự) nằm gọn 1 bên đường chia."""
    tot: Counter = Counter()
    one_side: Counter = Counter()
    for b in parsed.blocks:
        if b.kind not in ("text", "list", "heading") or len(b.text) < 30:
            continue
        split = parsed.page_sizes.get(b.page, (595, 842))[0] * split_x
        tot[b.page] += 1
        if b.bbox[2] <= split + 8 or b.bbox[0] >= split - 8:
            one_side[b.page] += 1
    return {p: one_side[p] / n >= 0.6 for p, n in tot.items()}


def recover_orphans(parsed: ParsedDoc, layer: TextLayer, out: dict[str, list[Block]], langinfo: dict) -> int:
    """Chữ trong text layer không thuộc khối nào (Docling bỏ sót) → khối text mới, chèn theo vị trí y."""
    margin = ingestion_cfg()["header_footer"]["margin_ratio"]
    left, right = langinfo["left"], langinfo["right"]
    by_page: dict[int, list[tuple]] = {}
    for lg, bl in out.items():
        for b in bl:
            by_page.setdefault(b.page, []).append(b.bbox)
    for b in parsed.removed + parsed.furniture:
        by_page.setdefault(b.page, []).append(b.bbox)
    for b in parsed.blocks:
        if b.kind in ("picture", "table"):
            by_page.setdefault(b.page, []).append(b.bbox)
    added = 0
    columnar = _columnar_pages(parsed, langinfo["split_x"])
    for page in range(1, parsed.n_pages + 1):
        if not columnar.get(page, True):
            continue
        w, h = parsed.page_sizes.get(page, (595, 842))
        split = w * langinfo["split_x"]
        boxes = by_page.get(page, [])
        orphans = []
        for ch, (l, t, r, bt) in layer.chars(page):
            if not ch.strip() or r <= l:
                continue
            cx, cy = (l + r) / 2, (t + bt) / 2
            if cy < h * margin or cy > h * (1 - margin):
                continue
            if any(x0 - 1.5 <= cx <= x1 + 1.5 and y0 - 1.5 <= cy <= y1 + 1.5 for x0, y0, x1, y1 in boxes):
                continue
            orphans.append((l, t, r, bt))
        if len(orphans) < 3:
            continue
        for side, (sl, sr) in ((left, (0.0, split)), (right, (split, w))):
            pts = sorted((o for o in orphans if sl <= (o[0] + o[2]) / 2 < sr), key=lambda o: o[1])
            groups: list[list[tuple]] = []
            for o in pts:
                if groups and o[1] - max(g[3] for g in groups[-1]) < 6:
                    groups[-1].append(o)
                else:
                    groups.append([o])
            for g in groups:
                if len(g) < 3:
                    continue
                bb = _union(g)
                text = layer.text(page, bb)
                if not text or not has_letters(text):
                    continue
                nb = Block("text", text, page, bb, label="orphan_text", lang=side, meta={"recovered": True})
                lst = out[side]
                pos = next((i for i, x in enumerate(lst) if x.page > page or (x.page == page and x.bbox[1] > bb[1])),
                           len(lst))
                lst.insert(pos, nb)
                added += 1
    return added


def pair_bilingual_headings(a: list[Block], b: list[Block], warnings: list[str]) -> None:
    """Heading cùng trang + cùng hàng (lệch ≤ 6pt): bên thiếu số mục nhận số của bên kia."""
    idx: dict[int, list[Block]] = {}
    for x in b:
        if x.kind in ("heading", "text", "list") and len(x.text) < 160:
            idx.setdefault(x.page, []).append(x)
    idx_a: dict[int, list[Block]] = {}
    for x in a:
        if x.kind in ("heading", "text", "list") and len(x.text) < 160:
            idx_a.setdefault(x.page, []).append(x)
    for src, other in ((a, idx), (b, idx_a)):
        for h in src:
            if h.kind != "heading":
                continue
            m = _NUM_PREFIX.match(h.text)
            if not m:
                continue
            cands = [x for x in other.get(h.page, []) if abs(x.bbox[1] - h.bbox[1]) <= 6]
            if not cands:
                continue
            x = min(cands, key=lambda x: abs(x.bbox[1] - h.bbox[1]))
            if not _NUM_PREFIX.match(x.text):
                x.text = f"{m.group(1)} {x.text}"
                x.meta["number_from_pair"] = True
            x.kind = "heading"


# ---------------------------------------------------------------- hậu xử lý theo ngôn ngữ

def merge_same_line(blocks: list[Block]) -> list[Block]:
    """Gộp các khối text cùng dòng (VD 'U-value' + ': 5.6 W/m²·°K')."""
    out: list[Block] = []
    for b in blocks:
        p = out[-1] if out else None
        if (p and p.kind in ("text", "list") and b.kind == "text" and p.page == b.page and not p.page_end
                and abs(p.bbox[1] - b.bbox[1]) < 3 and abs(p.bbox[3] - b.bbox[3]) < 3 and b.bbox[0] >= p.bbox[2] - 2
                and b.bbox[0] - p.bbox[2] < 200):
            p.text = f"{p.text} {b.text}"
            p.bbox = _union([p.bbox, b.bbox])
            continue
        out.append(b)
    return out


def _content(blocks: list[Block], i: int, step: int) -> int | None:
    j = i + step
    while 0 <= j < len(blocks):
        if blocks[j].kind not in ("picture",):
            return j
        j += step
    return None


def merge_page_break_tables(blocks: list[Block], warnings: list[str]) -> list[Block]:
    """Bảng cuối trang p + bảng đầu trang p+1, cùng số cột → 1 bảng (bỏ header lặp).
    Cho phép ≤ 2 khối ngắn (caption/ghi chú, không phải heading) của trang p nằm giữa 2 bảng."""
    out: list[Block] = []
    for b in blocks:
        if b.kind == "table" and b.rows and not any(x.page == b.page for x in out):
            k = len(out) - 1
            between = []
            while k >= 0 and out[k].kind != "table" and len(between) < 3:
                between.append(out[k])
                k -= 1
            prev = out[k] if k >= 0 else None
            ok_between = len(between) <= 2 and all(x.kind in ("text", "caption", "list") and len(x.text) < 200
                                                     for x in between)
            if (prev is not None and prev.kind == "table" and ok_between and b.page == prev.last_page + 1
                    and prev.rows and len(prev.rows[0]) == len(b.rows[0])):
                rows = b.rows
                prev.meta.setdefault("row_pages", [prev.page] * len(prev.rows))
                hdr = prev.rows[: prev.header_rows] if prev.header_rows else prev.rows[:1]
                if rows[: len(hdr)] == hdr:
                    rows = rows[len(hdr):]
                    prev.meta["repeated_header_dropped"] = True
                prev.rows = prev.rows + rows
                prev.meta["row_pages"] = prev.meta["row_pages"] + [b.page] * len(rows)
                if not prev.header_rows and len(prev.rows) - len(rows) == 1:
                    prev.header_rows = 1  # dòng đơn cuối trang trước = header của bảng tiếp
                prev.page_end = b.last_page
                prev.meta.setdefault("pages", [prev.page]).append(b.page)
                if b.meta.get("caption"):
                    prev.meta["caption"] = b.meta["caption"]
                continue
        out.append(b)
    return out


def attach_captions(blocks: list[Block], prof: dict) -> list[Block]:
    """Gắn số bảng từ caption 'Bảng x.y' liền kề (bỏ qua ≤ 1 khối ngắn kiểu '(Source: …)')."""
    pat = re.compile(prof["caption_pattern"])
    pos = prof.get("caption", "auto")
    used: set[int] = set()

    def find(i: int, step: int) -> int | None:
        j, skipped = i + step, 0
        while 0 <= j < len(blocks) and skipped <= 1:
            x = blocks[j]
            if x.kind == "table" or (x.kind == "heading" and not pat.match(x.text)):
                return None
            if x.kind in ("text", "caption", "heading") and pat.match(x.text) and j not in used:
                return j
            if x.kind in ("text", "caption") and len(x.text) < 160:
                skipped += 1
                j += step
                continue
            return None
        return None

    for i, b in enumerate(blocks):
        if b.kind != "table":
            continue
        order = {"below": (1,), "above": (-1,), "auto": (-1, 1)}[pos]
        for step in order:
            j = find(i, step)
            if j is not None:
                m = pat.match(blocks[j].text)
                b.meta["caption"] = blocks[j].text
                b.number = m.group("num")
                blocks[j].kind = "caption"
                blocks[j].meta["caption_of"] = i
                used.add(j)
                break
    return blocks


_END_PUNCT = re.compile(r"[.:;!?…)\]”\"]\s*$")


def merge_page_continuations(blocks: list[Block]) -> list[Block]:
    """Đoạn bị ngắt trang: khối cuối trang p chưa kết câu + khối đầu trang p+1 bắt đầu chữ thường → 1 khối."""
    out: list[Block] = []
    for b in blocks:
        p = out[-1] if out else None
        if (p and p.kind in ("text", "list") and b.kind in ("text", "list") and b.page == p.last_page + 1
                and not any(x.page == b.page for x in out) and not _END_PUNCT.search(p.text)
                and b.text[:1].islower()):
            p.text = f"{p.text} {b.text}"
            p.page_end = b.page
            p.meta["page_continuation"] = True
            continue
        out.append(b)
    return out


def postprocess(blocks: list[Block], prof: dict, warnings: list[str]) -> list[Block]:
    blocks = merge_same_line(blocks)
    blocks = merge_page_continuations(blocks)
    blocks = merge_page_break_tables(blocks, warnings)
    return attach_captions(blocks, prof)
