"""Bước 1 – Metadata tài liệu: đường dẫn thư mục + tên file + trang bìa (2–3 trang đầu).

Trường không suy ra được → None + warning (không đoán).
doc_id   = "doc_" + sha256(file)[:12]
doc_code = f"{project_id}_{building}_{stream}_{primary_discipline}_{doc_type}"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ingestion.parse import Block
from ingestion.textutil import ingestion_cfg
from shared.config import Settings, get_settings
from shared.keys import normalize_alias


@dataclass
class DocMeta:
    doc_id: str
    file_hash: str
    source_path: str
    project_id: str | None = None
    project_name: str | None = None
    building: str | None = None
    stream: str | None = None
    doc_type: str | None = None
    disciplines: list[str] = field(default_factory=list)
    title: str | None = None
    issuer: str | None = None
    revision: str | None = None
    issue_date: date | None = None
    stage: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def doc_code(self) -> str | None:
        parts = [self.project_id, self.building, self.stream, self.disciplines[0] if self.disciplines else None,
                 self.doc_type]
        return "_".join(parts) if all(parts) else None

    @property
    def project_label(self) -> str:
        return " ".join(x for x in (self.project_name or self.project_id, self.building) if x) or "?"


def make_doc_id(file_hash: str) -> str:
    return "doc_" + file_hash[:12]


# ---------------------------------------------------------------- dự án / thư mục

def _find_folder(path: Path, settings: Settings) -> tuple[dict | None, dict | None, str | None]:
    """→ (project, folder_map entry, building từ source_root)."""
    parts = [p for p in path.resolve().parts]
    for proj in settings.project_list():
        building = None
        for b in proj.get("buildings", []):
            if b.get("source_root") and b["source_root"] in parts:
                building = b["building"]
        fm = proj.get("folder_map", {})
        for part in reversed(parts[:-1]):
            if part in fm:
                return proj, fm[part], building
        if building:
            return proj, None, building
    # chỉ khớp tên thư mục (không có source_root): dùng folder_map của dự án đầu tiên có khoá đó
    for proj in settings.project_list():
        for part in reversed(parts[:-1]):
            if part in proj.get("folder_map", {}):
                return None, proj["folder_map"][part], None
    return None, None, None


def _alias_match(text: str, settings: Settings) -> tuple[str | None, str | None]:
    """Đếm alias dự án/công trình xuất hiện trong text (đã chuẩn hoá) → (project_id, building)."""
    norm = normalize_alias(text)
    best, best_score, building = None, 0, None
    for proj in settings.project_list():
        aliases = [proj["project_id"], proj["name"], *proj.get("aliases", [])]
        score = sum(1 for a in aliases if len(normalize_alias(a)) >= 4 and normalize_alias(a) in norm)
        if score > best_score:
            best, best_score = proj, score
    if best:
        spaced = _spaced(text)
        for b in best.get("buildings", []):
            for a in [b["building"], *b.get("aliases", [])]:
                na = _spaced(a).strip()
                if na and re.search(rf"(?<![a-z0-9]){re.escape(na)}(?![a-z0-9])", spaced):
                    building = b["building"]
                    break
            if building:
                break
    return (best["project_id"] if best else None), building


def _spaced(text: str) -> str:
    """Bỏ dấu, lowercase, ký tự đặc biệt → khoảng trắng (giữ ranh giới từ)."""
    from shared.keys import strip_accents

    return " " + re.sub(r"[^a-z0-9]+", " ", strip_accents(text).lower()) + " "


# ---------------------------------------------------------------- revision / ngày / giai đoạn

def _norm_rev(s: str) -> str:
    return re.sub(r"(?i)^(rev(ision)?|r)[.\s_\-]*", "", s.strip()).strip() or s.strip()


def revision_from(filename: str, cover_text: str, cover_tables: list[Block]) -> tuple[str | None, str | None]:
    pats = [re.compile(p) for p in ingestion_cfg()["metadata"]["revision_patterns"]]
    for p in pats:
        m = p.search(filename)
        if m:
            return _norm_rev(m.group("rev")), "filename"
    # bảng theo dõi phát hành trên trang bìa: cột "Revision / Hiệu chỉnh"
    for t in cover_tables:
        rows = t.rows or []
        if len(rows) < 2:
            continue
        hdr = rows[0]
        for ci, h in enumerate(hdr):
            if re.search(r"(?i)revision|hiệu chỉnh|phiên bản", h):
                vals = [r[ci] for r in rows[1:] if ci < len(r) and r[ci].strip()]
                if vals:
                    return _norm_rev(vals[-1]), "cover_table"
    for p in pats:
        m = p.search(cover_text)
        if m:
            return _norm_rev(m.group("rev")), "cover"
    return None, None


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        if 1990 <= y <= 2100:
            return date(y, m, d)
    except ValueError:
        return None
    return None


def issue_date_from(filename: str, cover_text: str) -> tuple[date | None, str | None]:
    """→ (ngày, ghi chú). Ưu tiên ngày đầy đủ trong tên file, rồi trang bìa; chỉ có tháng → ngày 1 + ghi chú."""
    for m in re.finditer(r"(?<!\d)(\d{8})(?!\d)", filename):
        s = m.group(1)
        d = _valid(int(s[:4]), int(s[4:6]), int(s[6:])) or _valid(int(s[4:]), int(s[2:4]), int(s[:2]))
        if d:
            return d, None
    for m in re.finditer(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)", cover_text):
        d = _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            return d, None
    for m in re.finditer(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", filename):
        return date(int(m.group(1)), int(m.group(2)), 1), "issue_date chỉ có tháng (tên file) → ngày 01"
    months = "january february march april may june july august september october november december".split()
    m = re.search(r"(?i)\b(" + "|".join(months) + r")\s+(20\d{2})\b", cover_text)
    if m:
        return date(int(m.group(2)), months.index(m.group(1).lower()) + 1, 1), "issue_date chỉ có tháng (bìa) → ngày 01"
    m = re.search(r"(?<!\d)(0?[1-9]|1[0-2])[\-/](20\d{2})(?!\d)", cover_text)
    if m:
        return date(int(m.group(2)), int(m.group(1)), 1), "issue_date chỉ có tháng (bìa) → ngày 01"
    return None, None


def stage_from(text: str) -> str | None:
    for pat, val in ingestion_cfg()["metadata"]["stage_patterns"]:
        if re.search(pat, text):
            return val
    return None


def issuer_from(text: str) -> str | None:
    for pat, val in ingestion_cfg()["metadata"]["issuers"]:
        if re.search(pat, text):
            return val
    return None


_TITLE_EXCLUDE = re.compile(r"(?i)^(dự án|công trình|địa điểm|project|tên dự án|location|chủ đầu tư|owner)\b")


def title_from(blocks: list[Block], filename: str) -> str:
    cands = [b for b in blocks if b.page == 1 and b.kind in ("heading", "text") and not _TITLE_EXCLUDE.match(b.text)
             and len(b.text.split()) >= 2 and len(b.text) >= 12 and sum(c.isalpha() for c in b.text) > 8]
    heads = [b for b in cands if b.kind == "heading"] or cands
    if heads:
        return heads[0].text[:300]
    return re.sub(r"[_\-]+", " ", Path(filename).stem).strip()


# ---------------------------------------------------------------- tổng hợp

def infer_metadata(path: str | Path, file_hash: str, blocks: list[Block], *, project: str | None = None,
                   building: str | None = None, settings: Settings | None = None) -> DocMeta:
    s = settings or get_settings()
    path = Path(path)
    meta = DocMeta(make_doc_id(file_hash), file_hash, str(path.resolve()))
    w = meta.warnings
    cover_blocks = [b for b in blocks if b.page <= 3]
    cover_text = "\n".join(b.text for b in cover_blocks if b.kind != "table") + "\n" + "\n".join(
        " ".join(" ".join(r) for r in (b.rows or [])) for b in cover_blocks if b.kind == "table")
    cover_tables = [b for b in blocks if b.page <= 2 and b.kind == "table"]

    proj, fm, bld = _find_folder(path, s)
    a_proj, a_bld = _alias_match(path.name + "\n" + cover_text, s)
    meta.project_id = project or (proj["project_id"] if proj else None) or a_proj
    meta.building = building or bld or a_bld
    pinfo = next((p for p in s.project_list() if p["project_id"] == meta.project_id), None)
    if pinfo:
        meta.project_name = (pinfo.get("aliases") or [pinfo["name"]])[0]
        if fm is None:
            fm = next((v for k, v in pinfo.get("folder_map", {}).items() if k in path.resolve().parts), None)
    if not meta.project_id:
        w.append("metadata: không suy ra được project_id (path/tên file/bìa) → null")
    if not meta.building:
        w.append("metadata: không suy ra được building → null")
    if fm:
        meta.stream, meta.doc_type, meta.disciplines = fm.get("stream"), fm.get("doc_type"), list(fm.get("disciplines", []))
    else:
        w.append("metadata: thư mục không có trong folder_map → stream/doc_type/disciplines null")

    meta.title = title_from(blocks, path.name)
    meta.issuer = issuer_from(cover_text)
    if not meta.issuer:
        w.append("metadata: không suy ra được issuer (đơn vị ban hành) → null")
    meta.revision, _src = revision_from(path.name, cover_text, cover_tables)
    if not meta.revision:
        w.append("metadata: không suy ra được revision → null")
    meta.issue_date, note = issue_date_from(path.name, cover_text)
    if note:
        w.append("metadata: " + note)
    if not meta.issue_date:
        w.append("metadata: không suy ra được issue_date → null")
    meta.stage = stage_from(path.name + "\n" + cover_text) or stage_from(str(path.resolve()))
    if not meta.stage:
        w.append("metadata: không suy ra được stage → null")
    return meta


def meta_summary(meta: DocMeta) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, date) else v) for k, v in meta.__dict__.items()} | {"doc_code": meta.doc_code}
