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

STREAM_ORDER = ("CONTRACT", "DESIGN_CONSULTANT", "OPERATOR_STANDARD", "LEGAL_STANDARD")


def tree(conn, project_id: str | None = None) -> list[dict[str, Any]]:
    """Kho theo dự án: [{project_id, name, start_date, end_date, n_series, n_documents, active_jobs,
    streams: [{stream, series: [{doc_code, title, building, disciplines, n_revisions, latest, unpublished,
    issue_date, updated_at}]}]}]. Luồng theo STREAM_ORDER; bộ mới cập nhật trước."""
    projects = db_projects(conn, [project_id] if project_id else None)
    args: tuple = (project_id,) if project_id else ()
    where = "WHERE project_id = %s" if project_id else ""
    docs = conn.execute(f"SELECT {DOC_FIELDS} FROM documents {where} "
                        "ORDER BY doc_code, issue_date NULLS FIRST, ingested_at", args).fetchall()
    jobs = {r["project_id"]: r["n"] for r in conn.execute(
        f"SELECT project_id, count(*) AS n FROM ingest_jobs {where} {'AND' if where else 'WHERE'} "
        "status IN ('QUEUED','PROCESSING','DRAFT','PUBLISHING','FAILED') GROUP BY project_id", args)}
    series: dict[str, list[dict]] = {}
    for d in docs:
        series.setdefault(d["doc_code"] or f"(không mã) {d['doc_id']}", []).append(d)
    out: dict[str, dict] = {}
    for p in projects:
        out[p["project_id"]] = {"project_id": p["project_id"], "name": p["name"], "start_date": p.get("start_date"),
                                "end_date": p.get("end_date"), "streams": {}}
    for code, revs in series.items():
        head = next((r for r in revs if r["is_latest"]), None) or max(revs, key=_version_key)
        pn = out.get(head["project_id"])
        if pn is None:  # dự án không còn trong DB (VD tài liệu CLI cũ) → vẫn hiện
            if project_id:
                continue
            pn = out[head["project_id"]] = {"project_id": head["project_id"], "name": head["project_id"],
                                            "start_date": None, "end_date": None, "streams": {}}
        latest = next((r for r in revs if r["is_latest"]), None)
        pn["streams"].setdefault(head["stream"] or "?", []).append({
            "doc_code": code, "title": head["title"], "building": head["building"],
            "disciplines": list(head["disciplines"] or []), "n_revisions": len(revs),
            "latest": _brief(latest) if latest else None, "unpublished": latest is None,
            "issue_date": head["issue_date"], "updated_at": max(r["ingested_at"] for r in revs),
        })
    result = []
    for pn in out.values():
        streams = pn.pop("streams")
        for items in streams.values():
            items.sort(key=lambda x: x["updated_at"], reverse=True)
        order = [s for s in STREAM_ORDER if s in streams] + sorted(set(streams) - set(STREAM_ORDER))
        pn["streams"] = [{"stream": s, "series": streams[s]} for s in order]
        pn["n_series"] = sum(len(v) for v in streams.values())
        pn["n_documents"] = sum(x["n_revisions"] for v in streams.values() for x in v)
        pn["active_jobs"] = jobs.get(pn["project_id"], 0)
        result.append(pn)
    return result


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


def delete_series(conn, s: Settings, doc_code: str) -> dict[str, Any]:
    """Xóa mọi revision của bộ (documents + parent_chunks cascade + điểm Qdrant). File gốc giữ lại."""
    with conn.transaction():
        ids = [r["doc_id"] for r in _series_rows(conn, doc_code)]
        conn.execute("DELETE FROM documents WHERE doc_code = %s", (doc_code,))
    client = qdrant(s)
    for doc_id in ids:
        delete_points(client, s, doc_id)
    return {"doc_code": doc_code, "deleted": ids}


# ---------------------------------------------------------------- sửa metadata

DOC_EDIT_FIELDS = ("title", "issuer", "revision", "issue_date", "stage")        # riêng từng revision
SERIES_EDIT_FIELDS = ("project_id", "building", "stream", "disciplines")       # chung cả bộ (xác định bộ)


def _clean(v: Any) -> Any:
    return (v.strip() or None) if isinstance(v, str) else v


def _set_payload(s: Settings, doc_ids: list[str], payload: dict[str, Any]) -> None:
    from qdrant_client import models as qm

    client = qdrant(s)
    for doc_id in doc_ids:
        flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
        for lang in ("vi", "en"):
            name = s.collection(lang)
            if client.collection_exists(name):
                client.set_payload(name, payload=payload, points=flt, wait=True)


def update_document(conn, s: Settings, doc_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Sửa metadata riêng của 1 revision (tiêu đề, đơn vị ban hành, revision, ngày ban hành, giai đoạn).
    is_latest giữ nguyên (đổi bản hiệu lực bằng set_latest). `revision` có trong payload Qdrant → đồng bộ."""
    upd = {k: _clean(fields[k]) for k in DOC_EDIT_FIELDS if k in fields}
    if not upd:
        raise KnowledgeError("không có trường nào để sửa", 400)
    with conn.transaction():
        if not conn.execute("SELECT 1 FROM documents WHERE doc_id = %s FOR UPDATE", (doc_id,)).fetchone():
            raise KnowledgeError(f"không có tài liệu {doc_id}", 404)
        conn.execute(f"UPDATE documents SET {', '.join(f'{k} = %s' for k in upd)} WHERE doc_id = %s",
                     (*upd.values(), doc_id))
    if "revision" in upd:
        _set_payload(s, [doc_id], {"revision": upd["revision"]})
    return {"doc_id": doc_id, "updated": upd}


def update_series(conn, s: Settings, doc_code: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Sửa thông tin chung của bộ cho mọi revision: dự án, công trình, luồng, phân hệ (Postgres + payload Qdrant).
    Chuyển sang dự án khác: công trình cũ không thuộc dự án mới → để trống (công trình không bắt buộc).
    doc_code giữ nguyên (khóa của bộ – revision mới vẫn nối đúng bộ)."""
    from app.models.document import Discipline, Stream

    upd = {k: _clean(fields[k]) for k in SERIES_EDIT_FIELDS if k in fields}
    if not upd:
        raise KnowledgeError("không có trường nào để sửa", 400)
    if "stream" in upd and upd["stream"] not in Stream.__members__:
        raise KnowledgeError(f"luồng không hợp lệ: {upd['stream']}", 400)
    if "disciplines" in upd:
        ds = list(dict.fromkeys(upd["disciplines"] or []))
        bad = [d for d in ds if d not in {x.value for x in Discipline}]
        if not ds or bad:
            raise KnowledgeError(f"phân hệ không hợp lệ: {bad or 'rỗng'}", 400)
        upd["disciplines"] = ds
    with conn.transaction():
        rows = _series_rows(conn, doc_code)
        cur = conn.execute("SELECT project_id, building FROM documents WHERE doc_code = %s LIMIT 1",
                           (doc_code,)).fetchone()
        pid, bld = upd.get("project_id", cur["project_id"]), upd.get("building", cur["building"])
        if "project_id" in upd and not conn.execute("SELECT 1 FROM projects WHERE project_id = %s",
                                                    (pid,)).fetchone():
            raise KnowledgeError(f"không có dự án {pid}", 400)
        has_b = bld and conn.execute("SELECT 1 FROM project_buildings WHERE project_id = %s AND building = %s",
                                     (pid, bld)).fetchone()
        if "building" in upd and bld and not has_b:
            raise KnowledgeError(f"dự án {pid} không có công trình {bld}", 400)
        if "project_id" in upd and "building" not in upd and bld and not has_b:
            upd["building"] = None
        conn.execute(f"UPDATE documents SET {', '.join(f'{k} = %s' for k in upd)} WHERE doc_code = %s",
                     (*upd.values(), doc_code))
    ids = [r["doc_id"] for r in rows]
    _set_payload(s, ids, upd)
    return {"doc_code": doc_code, "doc_ids": ids, "updated": upd}


def source_file(conn, doc_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT d.source_path, j.file_name FROM documents d LEFT JOIN ingest_jobs j "
                       "ON j.doc_id = d.doc_id AND j.status = 'PUBLISHED' WHERE d.doc_id = %s LIMIT 1",
                       (doc_id,)).fetchone()
    if not row:
        raise KnowledgeError(f"không có tài liệu {doc_id}", 404)
    return dict(row)
