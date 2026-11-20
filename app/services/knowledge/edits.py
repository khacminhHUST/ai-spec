"""Sửa tay dữ liệu đã lưu của 1 revision (soát lỗi sau xử lý AI).

- Mục (parent_chunks, Postgres): number, title, breadcrumb, content_md, trang, type. Không đổi section_key
  (khóa liên kết child + tham chiếu). Xóa mục = xóa cả child của mục.
- Child (Qdrant): raw_text, topic, trang → dựng lại `text` (breadcrumb + raw), embed lại, upsert cùng point id.
  Thêm child mới cho mục (seq kế tiếp), xóa child.
Quyền sửa: chưa phân quyền – kiểm tra đặt ở route (require_editor).
"""
from __future__ import annotations

from typing import Any

from qdrant_client import models as qm

from app.core.config import Settings
from app.db.client import qdrant
from app.models.document import ChildChunk, child_point_id
from app.providers.embedding import get_embedder
from app.repositories.index_store import EmbeddingCache, embed_children, upsert_children
from app.services.knowledge.errors import KnowledgeError
from app.services.knowledge.inspect import LANGS, _flt, _scroll, document_section
from app.utils.keys import child_seq, make_child_id
from app.utils.tokens import count_tokens

PARENT_FIELDS = ("number", "title", "breadcrumb", "content_md", "page_start", "page_end", "type")
CHILD_FIELDS = ("raw_text", "topic", "page_start", "page_end")


def _parent(conn, doc_id: str, lang: str, section_key: str) -> dict[str, Any]:
    if lang not in LANGS:
        raise KnowledgeError(f"ngôn ngữ không hợp lệ: {lang}")
    row = conn.execute("SELECT * FROM parent_chunks WHERE doc_id = %s AND lang = %s AND section_key = %s",
                       (doc_id, lang, section_key)).fetchone()
    if not row:
        raise KnowledgeError(f"không có mục {lang}/{section_key} trong {doc_id}", 404)
    return dict(row)


# ---------------------------------------------------------------- mục (parent)

def update_section(conn, s: Settings, doc_id: str, lang: str, section_key: str, fields: dict[str, Any],
                   client=None) -> dict[str, Any]:
    p = _parent(conn, doc_id, lang, section_key)
    upd = {k: v for k, v in fields.items() if k in PARENT_FIELDS}
    if "type" in upd and upd["type"] not in ("SECTION", "TABLE"):
        raise KnowledgeError("type phải là SECTION hoặc TABLE")
    if "content_md" in upd and not (upd["content_md"] or "").strip():
        raise KnowledgeError("nội dung mục không được trống")
    if upd:
        if "content_md" in upd:
            upd["token_count"] = count_tokens(upd["content_md"], lang)
        sets = ", ".join(f"{k} = %s" for k in upd)
        conn.execute(f"UPDATE parent_chunks SET {sets} WHERE parent_id = %s", (*upd.values(), p["parent_id"]))
    return document_section(conn, s, doc_id, lang, section_key, client)


def delete_section(conn, s: Settings, doc_id: str, lang: str, section_key: str, client=None) -> dict[str, Any]:
    p = _parent(conn, doc_id, lang, section_key)
    client = client or qdrant(s)
    client.delete(s.collection(lang), points_selector=qm.FilterSelector(filter=_flt(doc_id, p["parent_id"])),
                  wait=True)
    conn.execute("DELETE FROM parent_chunks WHERE parent_id = %s", (p["parent_id"],))
    return {"deleted": p["parent_id"]}


# ---------------------------------------------------------------- child

def _bc_line(parent: dict, topic: str | None) -> str:
    """Breadcrumb cho child mới (ingest dùng [dự án | luồng | phân hệ | mục | topic]; ở đây lấy breadcrumb mục)."""
    parts = [parent["breadcrumb"] or parent["title"] or parent["section_key"]]
    if topic:
        parts.append(topic)
    return "[" + " | ".join(parts) + "]"


def _retext(old: dict, raw: str, topic: str | None, parent: dict) -> str:
    """Giữ phần đầu (breadcrumb + header bảng) của text cũ, thay raw; đổi topic thì sửa ở dòng breadcrumb."""
    text, old_raw, old_topic = old.get("text") or "", old.get("raw_text") or "", old.get("topic")
    head = text[: len(text) - len(old_raw)] if old_raw and text.endswith(old_raw) else _bc_line(parent, old_topic) + "\n"
    if topic != old_topic:
        first, sep, rest = head.partition("\n")
        if first.startswith("[") and first.endswith("]"):
            inner = first[1:-1]
            if old_topic and inner.endswith(" | " + old_topic):
                inner = inner[: -len(" | " + old_topic)]
            first = "[" + inner + (f" | {topic}" if topic else "") + "]"
        head = first + sep + rest
    return head + raw


def _save_children(s: Settings, client, children: list[ChildChunk]) -> None:
    lang = children[0].lang
    vecs = embed_children(children, get_embedder(lang, s), EmbeddingCache())
    upsert_children(client, s, children, vecs)


def _child_payload(client, s: Settings, lang: str, doc_id: str, child_id: str) -> dict[str, Any]:
    pts = client.retrieve(s.collection(lang), ids=[child_point_id(child_id)], with_payload=True, with_vectors=False)
    if not pts or (pts[0].payload or {}).get("doc_id") != doc_id:
        raise KnowledgeError(f"không có child {child_id}", 404)
    return dict(pts[0].payload)


def update_child(conn, s: Settings, doc_id: str, lang: str, child_id: str, fields: dict[str, Any],
                 client=None) -> dict[str, Any]:
    client = client or qdrant(s)
    old = _child_payload(client, s, lang, doc_id, child_id)
    p = _parent(conn, doc_id, lang, old["section_key"])
    new = {**old, **{k: v for k, v in fields.items() if k in CHILD_FIELDS}}
    new["topic"] = (new.get("topic") or "").strip() or None
    if not (new["raw_text"] or "").strip():
        raise KnowledgeError("nội dung child không được trống – muốn bỏ thì xóa child")
    new["text"] = _retext(old, new["raw_text"], new["topic"], p)
    _save_children(s, client, [ChildChunk(**new)])
    return document_section(conn, s, doc_id, lang, old["section_key"], client)


def add_child(conn, s: Settings, doc_id: str, lang: str, section_key: str, fields: dict[str, Any],
              client=None) -> dict[str, Any]:
    client = client or qdrant(s)
    p = _parent(conn, doc_id, lang, section_key)
    raw = (fields.get("raw_text") or "").strip()
    if not raw:
        raise KnowledgeError("nội dung child không được trống")
    doc = conn.execute("SELECT project_id, building, stream, disciplines, revision, is_latest FROM documents "
                       "WHERE doc_id = %s", (doc_id,)).fetchone()
    kids = _scroll(client, s.collection(lang), _flt(doc_id, p["parent_id"]), ["child_id"])
    seq = max((child_seq(k["child_id"]) for k in kids), default=-1) + 1
    topic = (fields.get("topic") or "").strip() or None
    child = ChildChunk(
        child_id=make_child_id(p["parent_id"], seq), parent_id=p["parent_id"], section_key=section_key, lang=lang,
        type="TABLE_ROWS" if p["type"] == "TABLE" else "TEXT", text=_bc_line(p, topic) + "\n" + raw, raw_text=raw,
        topic=topic, page_start=fields.get("page_start") or p["page_start"],
        page_end=fields.get("page_end") or p["page_end"], doc_id=doc_id, project_id=doc["project_id"],
        building=doc["building"], stream=doc["stream"], disciplines=list(doc["disciplines"] or []),
        revision=doc["revision"], is_latest=doc["is_latest"],
    )
    _save_children(s, client, [child])
    return document_section(conn, s, doc_id, lang, section_key, client)


def delete_child(conn, s: Settings, doc_id: str, lang: str, child_id: str, client=None) -> dict[str, Any]:
    client = client or qdrant(s)
    old = _child_payload(client, s, lang, doc_id, child_id)
    client.delete(s.collection(lang), points_selector=qm.PointIdsList(points=[child_point_id(child_id)]), wait=True)
    return document_section(conn, s, doc_id, lang, old["section_key"], client)
