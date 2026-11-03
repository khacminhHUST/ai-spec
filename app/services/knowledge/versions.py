"""Quản lý phiên bản: cây kho, lịch sử revision, đặt bản hiệu lực, gỡ khỏi tìm kiếm, xóa revision.

is_latest luôn đổi ở Postgres trước (transaction) rồi payload Qdrant (set_latest_payload / delete_points có sẵn).
Thông tin thêm của revision (người/ngày phát hành) lấy bằng join ingest_jobs.doc_id; tài liệu nạp bằng CLI để trống.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.config import Settings
from app.db.client import qdrant
from app.repositories.index_store import _version_key, delete_points, set_latest_payload
from app.repositories.postgres import db_projects
from app.services.knowledge.errors import KnowledgeError

DOC_FIELDS = ("doc_id, doc_code, project_id, building, stream, disciplines, title, issuer, revision, issue_date, "
              "stage, languages, is_bilingual, is_latest, source_path, ingested_at")


# ---------------------------------------------------------------- cây kho

def tree(conn, project_id: str | None = None) -> list[dict[str, Any]]:
    """Dự án → công trình → luồng → phân hệ (chính) → bộ tài liệu {doc_code, title, n_revisions, latest}."""
    projects = db_projects(conn, [project_id] if project_id else None)
    args: tuple = (project_id,) if project_id else ()
    docs = conn.execute(f"SELECT {DOC_FIELDS} FROM documents {'WHERE project_id = %s' if project_id else ''} "
                        "ORDER BY doc_code, issue_date NULLS FIRST, ingested_at", args).fetchall()
    series: dict[str, list[dict]] = {}
    for d in docs:
        series.setdefault(d["doc_code"] or f"(không mã) {d['doc_id']}", []).append(d)
    nodes: dict[tuple, dict] = {}
    out = []
    for p in projects:
        pn = {"project_id": p["project_id"], "name": p["name"], "buildings": []}
        out.append(pn)
        for b in p["buildings"]:
            bn = {"building": b["building"], "streams": []}
            pn["buildings"].append(bn)
            nodes[(p["project_id"], b["building"])] = bn
    for code, revs in series.items():
        first = revs[-1]
        bn = nodes.get((first["project_id"], first["building"]))
        if bn is None:  # công trình không còn trong DB (VD tài liệu cũ) → vẫn hiện
            pn = next((x for x in out if x["project_id"] == first["project_id"]), None)
            if pn is None:
                if project_id:
                    continue
                pn = {"project_id": first["project_id"], "name": first["project_id"], "buildings": []}
                out.append(pn)
            bn = {"building": first["building"], "streams": []}
            pn["buildings"].append(bn)
            nodes[(first["project_id"], first["building"])] = bn
        latest = next((r for r in revs if r["is_latest"]), None)
        newest = max(revs, key=_version_key)
        sn = _child(bn["streams"], "stream", first["stream"] or "?", "disciplines")
        dn = _child(sn["disciplines"], "discipline", (first["disciplines"] or ["?"])[0], "series")
        dn["series"].append({
            "doc_code": code, "title": (latest or newest)["title"], "n_revisions": len(revs),
            "latest": _brief(latest) if latest else None, "unpublished": latest is None,
        })
    return out


def _child(items: list[dict], key: str, value: str, sub: str) -> dict:
    node = next((x for x in items if x[key] == value), None)
    if node is None:
        node = {key: value, sub: []}
        items.append(node)
    return node


def _brief(d: dict) -> dict:
    return {"doc_id": d["doc_id"], "revision": d["revision"], "issue_date": d["issue_date"], "stage": d["stage"]}


# ---------------------------------------------------------------- lịch sử

def series_history(conn, doc_code: str, date_from: date | None = None, date_to: date | None = None,
                   stage: str | None = None) -> dict[str, Any]:
    cond, args = ["d.doc_code = %s"], [doc_code]
    if date_from:
        cond.append("d.issue_date >= %s")
        args.append(date_from)
    if date_to:
        cond.append("d.issue_date <= %s")
        args.append(date_to)
    if stage:
        cond.append("d.stage = %s")
        args.append(stage)
    total = conn.execute("SELECT count(*) AS n, bool_or(is_latest) AS live FROM documents WHERE doc_code = %s",
                         (doc_code,)).fetchone()
    if not total["n"]:
        raise KnowledgeError(f"không có bộ tài liệu {doc_code}", 404)
    rows = conn.execute(
        f"SELECT {', '.join('d.' + c.strip() for c in DOC_FIELDS.split(','))}, "
        "j.job_id, j.file_name, j.uploaded_by, j.published_by, j.published_at "
        "FROM documents d LEFT JOIN LATERAL (SELECT * FROM ingest_jobs WHERE doc_id = d.doc_id AND status = 'PUBLISHED' "
        "ORDER BY published_at DESC NULLS LAST LIMIT 1) j ON true "
        f"WHERE {' AND '.join(cond)}", args).fetchall()
    revs = sorted((dict(r) for r in rows), key=_version_key, reverse=True)
    return {"doc_code": doc_code, "n_revisions": total["n"], "unpublished": not total["live"], "revisions": revs}


# ---------------------------------------------------------------- thao tác

def _series_rows(conn, doc_code: str) -> list[dict]:
    rows = conn.execute("SELECT doc_id, revision, issue_date, ingested_at, is_latest FROM documents "
                        "WHERE doc_code = %s FOR UPDATE", (doc_code,)).fetchall()
    if not rows:
        raise KnowledgeError(f"không có bộ tài liệu {doc_code}", 404)
    return rows


def _apply_latest(conn, rows: list[dict], latest_id: str | None) -> list[tuple[str, bool]]:
    """Đặt is_latest ở Postgres (trong transaction của người gọi) → danh sách thay đổi cho Qdrant."""
    changes = []
    for r in rows:
        want = r["doc_id"] == latest_id
        if r["is_latest"] != want:
            conn.execute("UPDATE documents SET is_latest = %s WHERE doc_id = %s", (want, r["doc_id"]))
            changes.append((r["doc_id"], want))
    return changes


def _sync_qdrant(s: Settings, changes: list[tuple[str, bool]]) -> None:
    client = qdrant(s)
    for doc_id, want in changes:
        set_latest_payload(client, s, doc_id, want)


def set_latest(conn, s: Settings, doc_code: str, doc_id: str) -> dict[str, Any]:
    """Bản được chọn is_latest = true, các bản khác cùng doc_code false (Postgres + Qdrant)."""
    with conn.transaction():
        rows = _series_rows(conn, doc_code)
        if doc_id not in {r["doc_id"] for r in rows}:
            raise KnowledgeError(f"{doc_id} không thuộc bộ {doc_code}", 404)
        changes = _apply_latest(conn, rows, doc_id)
    _sync_qdrant(s, changes)
    return {"doc_code": doc_code, "latest": doc_id, "changes": changes}


def unpublish(conn, s: Settings, doc_code: str) -> dict[str, Any]:
    """Gỡ khỏi tìm kiếm: mọi revision is_latest = false. Khôi phục = đặt lại bản hiệu lực."""
    with conn.transaction():
        changes = _apply_latest(conn, _series_rows(conn, doc_code), None)
    _sync_qdrant(s, changes)
    return {"doc_code": doc_code, "latest": None, "changes": changes}


def delete_revision(conn, s: Settings, doc_id: str) -> dict[str, Any]:
    """Xóa documents (cascade parent_chunks) + điểm Qdrant. Bản bị xóa đang hiệu lực → bản mới nhất còn lại lên thay.
    File gốc giữ lại."""
    with conn.transaction():
        doc = conn.execute("SELECT doc_id, doc_code, is_latest FROM documents WHERE doc_id = %s FOR UPDATE",
                           (doc_id,)).fetchone()
        if not doc:
            raise KnowledgeError(f"không có tài liệu {doc_id}", 404)
        conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
        changes: list[tuple[str, bool]] = []
        if doc["is_latest"] and doc["doc_code"]:
            rest = conn.execute("SELECT doc_id, revision, issue_date, ingested_at, is_latest FROM documents "
                                "WHERE doc_code = %s FOR UPDATE", (doc["doc_code"],)).fetchall()
            if rest:
                changes = _apply_latest(conn, rest, max(rest, key=_version_key)["doc_id"])
    delete_points(qdrant(s), s, doc_id)
    _sync_qdrant(s, changes)
    return {"deleted": doc_id, "doc_code": doc["doc_code"], "changes": changes}


def source_file(conn, doc_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT d.source_path, j.file_name FROM documents d LEFT JOIN ingest_jobs j "
                       "ON j.doc_id = d.doc_id AND j.status = 'PUBLISHED' WHERE d.doc_id = %s LIMIT 1",
                       (doc_id,)).fetchone()
    if not row:
        raise KnowledgeError(f"không có tài liệu {doc_id}", 404)
    return dict(row)
