"""Xem dữ liệu đã lưu của 1 revision (đọc thẳng Postgres + Qdrant, không cần artifact nháp).

Dùng để soát lỗi sau xử lý AI: metadata, số parent/child, cây mục, nội dung từng mục và payload từng child,
kèm kiểm tra đồng bộ Postgres ↔ Qdrant (mục không có child, child mồ côi, payload lệch metadata).
"""
from __future__ import annotations

import re
from typing import Any

from qdrant_client import models as qm

from app.core.config import Settings
from app.db.client import qdrant
from app.services.knowledge.errors import KnowledgeError
from app.utils.keys import child_seq

LANGS = ("vi", "en")
# payload Qdrant phải khớp cột documents (ChildChunk: payload lọc)
SYNC_FIELDS = ("project_id", "building", "stream", "revision", "is_latest", "disciplines")
SCROLL_PAGE = 2000
MAX_LIST = 50


def _doc(conn, doc_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
    if not row:
        raise KnowledgeError(f"không có tài liệu {doc_id}", 404)
    return dict(row)


def _flt(doc_id: str, parent_id: str | None = None) -> qm.Filter:
    must = [qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
    if parent_id:
        must.append(qm.FieldCondition(key="parent_id", match=qm.MatchValue(value=parent_id)))
    return qm.Filter(must=must)


def _scroll(client, collection: str, flt: qm.Filter, fields: list[str] | bool) -> list[dict[str, Any]]:
    out, offset = [], None
    while True:
        pts, offset = client.scroll(collection, scroll_filter=flt, limit=SCROLL_PAGE, offset=offset,
                                    with_payload=fields, with_vectors=False)
        out.extend(p.payload or {} for p in pts)
        if offset is None:
            return out


def _natural(key: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", key)]


def _order(p: dict) -> tuple:
    return (p["page_start"] is None, p["page_start"] or 0, _natural(p["section_key"]))


# ---------------------------------------------------------------- tổng quan

def document_detail(conn, s: Settings, doc_id: str, client=None) -> dict[str, Any]:
    """Metadata (documents), job đã phát hành (nếu có), thống kê theo ngôn ngữ, các kiểm tra đồng bộ."""
    doc = _doc(conn, doc_id)
    job = conn.execute(
        "SELECT job_id, file_name, size, doc_type, uploaded_by, created_at, published_by, published_at, report "
        "FROM ingest_jobs WHERE doc_id = %s AND status = 'PUBLISHED' ORDER BY published_at DESC NULLS LAST LIMIT 1",
        (doc_id,)).fetchone()
    parents = conn.execute("SELECT parent_id, lang, type, section_key, token_count FROM parent_chunks "
                           "WHERE doc_id = %s", (doc_id,)).fetchall()
    client = client or qdrant(s)
    langs, checks = [], []
    for lang in LANGS:
        ps = [p for p in parents if p["lang"] == lang]
        kids = _scroll(client, s.collection(lang), _flt(doc_id), ["parent_id", "type", *SYNC_FIELDS])
        if not ps and not kids:
            continue
        pids = {p["parent_id"] for p in ps}
        n_by_parent: dict[str, int] = {}
        for k in kids:
            n_by_parent[k.get("parent_id")] = n_by_parent.get(k.get("parent_id"), 0) + 1
        empty = sorted((p["section_key"] for p in ps if p["parent_id"] not in n_by_parent), key=_natural)
        orphans = sum(n for pid, n in n_by_parent.items() if pid not in pids)
        mismatch: dict[str, int] = {}
        for k in kids:
            for f in SYNC_FIELDS:
                want = list(doc[f] or []) if f == "disciplines" else doc[f]
                got = k.get(f) or [] if f == "disciplines" else k.get(f)
                if got != want:
                    mismatch[f] = mismatch.get(f, 0) + 1
        langs.append({
            "lang": lang,
            "n_parents": len(ps),
            "n_sections": sum(p["type"] == "SECTION" for p in ps),
            "n_tables": sum(p["type"] == "TABLE" for p in ps),
            "parent_tokens": sum(p["token_count"] for p in ps),
            "n_children": len(kids),
            "n_children_text": sum(k.get("type") == "TEXT" for k in kids),
            "n_children_table": sum(k.get("type") == "TABLE" for k in kids),
            "empty_parents": empty[:MAX_LIST],
            "n_empty_parents": len(empty),
            "orphan_children": orphans,
            "payload_mismatch": mismatch,
        })
        tag = lang.upper()
        if ps and not kids:
            checks.append({"level": "error", "message": f"{tag}: có {len(ps)} mục ở Postgres nhưng không có child "
                                                         "trên Qdrant – tài liệu không tìm được"})
        elif empty:
            checks.append({"level": "warn", "message": f"{tag}: {len(empty)} mục không có child (không tìm thấy "
                                                        "bằng tìm kiếm, chỉ nạp kèm theo mục cha/tham chiếu)"})
        if orphans:
            checks.append({"level": "error", "message": f"{tag}: {orphans} child trên Qdrant không có mục cha ở Postgres"})
        for f, n in mismatch.items():
            checks.append({"level": "error", "message": f"{tag}: {n} child có payload '{f}' lệch với metadata tài liệu"})
    if not langs:
        checks.append({"level": "error", "message": "Không có mục nào được lưu"})
    if not doc["title"] or re.fullmatch(r"[0-9a-f]{32,}", (doc["title"] or "").strip()):
        checks.append({"level": "warn", "message": "Tiêu đề trống hoặc là mã băm – nên sửa metadata"})
    for f, label in (("revision", "revision"), ("issue_date", "ngày ban hành")):
        if not doc[f]:
            checks.append({"level": "warn", "message": f"Chưa nhận diện được {label}"})
    if not doc["disciplines"]:
        checks.append({"level": "warn", "message": "Chưa có phân hệ"})
    doc.pop("source_path", None)
    return {"document": doc, "job": dict(job) if job else None, "langs": langs, "checks": checks}


# ---------------------------------------------------------------- cây mục / chi tiết mục

def document_sections(conn, s: Settings, doc_id: str, q: str | None = None, client=None) -> list[dict[str, Any]]:
    """Mọi mục (SECTION + TABLE) theo trang, kèm số child; q: lọc theo số mục / tiêu đề / nội dung."""
    _doc(conn, doc_id)
    sql = ("SELECT parent_id, lang, section_key, parent_section_key, type, number, title, page_start, page_end, "
           "token_count, cardinality(referenced_ids) AS n_refs FROM parent_chunks WHERE doc_id = %s")
    args: list[Any] = [doc_id]
    if q and q.strip():
        like = f"%{q.strip()}%"
        sql += " AND (number ILIKE %s OR title ILIKE %s OR section_key ILIKE %s OR content_md ILIKE %s)"
        args += [like] * 4
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    client = client or qdrant(s)
    n: dict[str, int] = {}
    for lang in {r["lang"] for r in rows}:
        for k in _scroll(client, s.collection(lang), _flt(doc_id), ["parent_id"]):
            n[k.get("parent_id")] = n.get(k.get("parent_id"), 0) + 1
    for r in rows:
        r["n_children"] = n.get(r.pop("parent_id"), 0)
    return sorted(rows, key=_order)


def document_section(conn, s: Settings, doc_id: str, lang: str, section_key: str, client=None) -> dict[str, Any]:
    """Bản ghi parent đầy đủ (Postgres) + payload đầy đủ của từng child (Qdrant, không vector)."""
    if lang not in LANGS:
        raise KnowledgeError(f"ngôn ngữ không hợp lệ: {lang}")
    parent = conn.execute("SELECT * FROM parent_chunks WHERE doc_id = %s AND lang = %s AND section_key = %s",
                          (doc_id, lang, section_key)).fetchone()
    if not parent:
        raise KnowledgeError(f"không có mục {lang}/{section_key} trong {doc_id}", 404)
    client = client or qdrant(s)
    kids = _scroll(client, s.collection(lang), _flt(doc_id, parent["parent_id"]), True)
    kids.sort(key=lambda c: child_seq(c["child_id"]))
    return {"parent": dict(parent), "children": kids}
