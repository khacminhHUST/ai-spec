"""ingest_jobs: tạo job từ upload, quy tắc doc_code, chuyển trạng thái, truy vấn.

Trạng thái: QUEUED → PROCESSING → DRAFT → PUBLISHING → PUBLISHED; nhánh phụ FAILED, CANCELLED.
PUBLISHING có step = 'queued' khi chờ worker (worker nhận → 'embed').
Hàm nhận `conn` autocommit (pg_connect(..., autocommit=True)); `conn.transaction()` là transaction thật.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import load_yaml
from app.models.document import Discipline, Stream
from app.services.knowledge.errors import KnowledgeError
from app.services.knowledge.storage import FileStore

STATUSES = ("QUEUED", "PROCESSING", "DRAFT", "PUBLISHING", "PUBLISHED", "FAILED", "CANCELLED")
ACTIVE = ("QUEUED", "PROCESSING", "DRAFT", "PUBLISHING", "FAILED")   # còn giữ file_hash (khớp index unique)
ACTIONS: dict[str, tuple[frozenset[str], str]] = {
    "publish": (frozenset({"DRAFT"}), "PUBLISHING"),
    # PROCESSING: worker thấy CANCELLED thì dừng (worker.py); PUBLISHING chỉ khi còn chờ (step=queued)
    "cancel": (frozenset({"QUEUED", "PROCESSING", "DRAFT", "PUBLISHING", "FAILED"}), "CANCELLED"),
    "retry": (frozenset({"FAILED"}), "QUEUED"),
}
PUBLISH_QUEUED = "queued"
OPTIONAL_FIELDS = ("revision", "issue_date", "stage", "title", "issuer")




# ---------------------------------------------------------------- quy tắc thuần (unit test không cần DB)

def base_doc_code(project_id: str, building: str | None, stream: str, disciplines: list[str] | None,
                  doc_type: str | None) -> str:
    """Đủ công trình + phân hệ + loại tài liệu → như DocMeta.doc_code (project_building_stream_discipline_doctype).
    Upload gọn (chỉ dự án + luồng, phân loại để worker tự nhận) → project_stream; trùng thì next_doc_code thêm _2…"""
    if building and disciplines and doc_type:
        return "_".join([project_id, building, stream, disciplines[0], doc_type])
    return f"{project_id}_{stream}"


def next_doc_code(base: str, existing: set[str]) -> str:
    """Tài liệu mới: trùng bộ đã có → thêm hậu tố _2, _3… để không thay nhầm."""
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def check_transition(status: str, action: str) -> str:
    """→ trạng thái mới; không hợp lệ → KnowledgeError 409."""
    if action not in ACTIONS:
        raise KnowledgeError(f"thao tác không hỗ trợ: {action}")
    allowed, new = ACTIONS[action]
    if status not in allowed:
        raise KnowledgeError(f"không thể {action} job ở trạng thái {status} (cần {'/'.join(sorted(allowed))})", 409)
    return new


def clean_classification(md: dict[str, Any], doc_types: list[str] | None = None) -> dict[str, Any]:
    """doc_type / disciplines (tùy chọn) → chuẩn hóa; giá trị lạ → KnowledgeError. Thiếu → None / []."""
    doc_types = doc_types if doc_types is not None else list(load_yaml("headings").get("profiles") or {})
    out: dict[str, Any] = {"doc_type": str(md.get("doc_type") or "").strip() or None}
    if out["doc_type"] and out["doc_type"] not in doc_types:
        raise KnowledgeError(f"loại tài liệu không hợp lệ: {out['doc_type']} (chọn: {', '.join(doc_types)})")
    disc = list(dict.fromkeys(str(d).strip() for d in md.get("disciplines") or [] if str(d).strip()))
    if bad := [d for d in disc if d not in Discipline.__members__]:
        raise KnowledgeError(f"phân hệ không hợp lệ: {bad}")
    out["disciplines"] = disc
    return out


def validate_metadata(md: dict[str, Any], doc_types: list[str] | None = None) -> dict[str, Any]:
    """Kiểm tra form upload (không cần DB) → metadata đã chuẩn hóa.
    Bắt buộc: dự án, luồng. Công trình, loại tài liệu, phân hệ tùy chọn (thiếu → worker tự nhận diện)."""
    out: dict[str, Any] = {}
    for k in ("project_id", "stream"):
        v = str(md.get(k) or "").strip()
        if not v:
            raise KnowledgeError(f"thiếu trường bắt buộc: {k}")
        out[k] = v
    if out["stream"] not in Stream.__members__:
        raise KnowledgeError(f"luồng không hợp lệ: {out['stream']}")
    out["building"] = str(md.get("building") or "").strip() or None
    out.update(clean_classification(md, doc_types))
    for k in OPTIONAL_FIELDS:
        v = md.get(k)
        out[k] = str(v).strip() if v not in (None, "") else None
    if out["issue_date"]:
        try:
            out["issue_date"] = date.fromisoformat(out["issue_date"])
        except ValueError:
            raise KnowledgeError(f"ngày ban hành không hợp lệ: {out['issue_date']} (YYYY-MM-DD)") from None
    series = md.get("series") or {"mode": "new"}
    if series.get("mode") not in ("new", "revision"):
        raise KnowledgeError("bộ tài liệu: mode phải là new hoặc revision")
    if series["mode"] == "revision" and not series.get("doc_code"):
        raise KnowledgeError("chọn bộ tài liệu cần thêm revision")
    out["series"] = {"mode": series["mode"], "doc_code": series.get("doc_code")}
    return out


# ---------------------------------------------------------------- DB

def find_duplicate(conn, file_hash: str) -> str | None:
    """Trùng tài liệu đã phát hành hoặc job còn hiệu lực → thông báo; không trùng → None."""
    d = conn.execute("SELECT doc_id, doc_code FROM documents WHERE file_hash = %s", (file_hash,)).fetchone()
    if d:
        return f"file đã có trong kho: {d['doc_id']} (bộ {d['doc_code']})"
    j = conn.execute("SELECT job_id, status FROM ingest_jobs WHERE file_hash = %s AND status = ANY(%s)",
                     (file_hash, list(ACTIVE))).fetchone()
    if j:
        return f"file đã có job {j['job_id']} ({j['status']})"
    return None


def resolve_series(conn, md: dict[str, Any]) -> str:
    """doc_code của job theo lựa chọn bộ tài liệu."""
    if md["series"]["mode"] == "revision":
        code = md["series"]["doc_code"]
        row = conn.execute("SELECT project_id FROM documents WHERE doc_code = %s LIMIT 1", (code,)).fetchone()
        if not row:
            raise KnowledgeError(f"không có bộ tài liệu {code}", 404)
        if row["project_id"] != md["project_id"]:
            raise KnowledgeError(f"bộ {code} thuộc dự án {row['project_id']}, không phải {md['project_id']}")
        return code
    base = base_doc_code(md["project_id"], md["building"], md["stream"], md["disciplines"], md["doc_type"])
    rows = conn.execute(
        "SELECT doc_code AS c FROM documents WHERE doc_code LIKE %(p)s "
        "UNION SELECT series_doc_code FROM ingest_jobs WHERE series_doc_code LIKE %(p)s AND status = ANY(%(a)s)",
        {"p": base.replace("_", r"\_") + "%", "a": list(ACTIVE)}).fetchall()
    return next_doc_code(base, {r["c"] for r in rows})


def inherit_series(conn, md: dict[str, Any]) -> None:
    """Revision mới của bộ đã có: công trình / phân hệ / loại tài liệu còn trống → lấy theo bộ (không phải nhận diện
    lại). Loại tài liệu chỉ có ở ingest_jobs (tài liệu nạp bằng CLI không có → để worker nhận diện)."""
    if md["series"]["mode"] != "revision":
        return
    code = md["series"]["doc_code"]
    d = conn.execute("SELECT building, disciplines FROM documents WHERE doc_code = %s ORDER BY is_latest DESC LIMIT 1",
                     (code,)).fetchone()
    if d:
        md["building"] = md["building"] or d["building"]
        md["disciplines"] = md["disciplines"] or list(d["disciplines"] or [])
    if not md["doc_type"]:
        j = conn.execute("SELECT doc_type FROM ingest_jobs WHERE series_doc_code = %s AND doc_type IS NOT NULL "
                         "ORDER BY created_at DESC LIMIT 1", (code,)).fetchone()
        md["doc_type"] = j["doc_type"] if j else None


def create_job(conn, store: FileStore, *, file_name: str, src: Path, file_hash: str, size: int,
               metadata: dict[str, Any], user_id: str | None) -> dict[str, Any]:
    md = validate_metadata(metadata)
    if not conn.execute("SELECT 1 FROM projects WHERE project_id = %s", (md["project_id"],)).fetchone():
        raise KnowledgeError(f"không có dự án {md['project_id']}", 404)
    if md["building"] and not conn.execute("SELECT 1 FROM project_buildings WHERE project_id = %s AND building = %s",
                                           (md["project_id"], md["building"])).fetchone():
        raise KnowledgeError(f"không có công trình {md['building']} trong dự án {md['project_id']}", 404)
    if dup := find_duplicate(conn, file_hash):
        raise KnowledgeError(dup, 409)
    key = store.put(md["project_id"], file_hash, src)
    job_id = "job_" + uuid.uuid4().hex[:12]
    with conn.transaction():
        code = resolve_series(conn, md)
        inherit_series(conn, md)
        conn.execute(
            "INSERT INTO ingest_jobs (job_id, file_name, file_hash, storage_key, size, project_id, building, stream, "
            "disciplines, doc_type, revision, issue_date, stage, title, issuer, series_doc_code, status, uploaded_by) "
            "VALUES (%(job_id)s, %(file_name)s, %(file_hash)s, %(key)s, %(size)s, %(project_id)s, %(building)s, "
            "%(stream)s, %(disciplines)s, %(doc_type)s, %(revision)s, %(issue_date)s, %(stage)s, %(title)s, "
            "%(issuer)s, %(code)s, 'QUEUED', %(user)s)",
            {**md, "job_id": job_id, "file_name": file_name, "file_hash": file_hash, "key": key, "size": size,
             "code": code, "user": user_id})
    return get_job(conn, job_id)


def get_job(conn, job_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM ingest_jobs WHERE job_id = %s", (job_id,)).fetchone()
    if not row:
        raise KnowledgeError(f"không có job {job_id}", 404)
    return dict(row)


def replaced_doc(conn, job: dict[str, Any]) -> dict[str, Any] | None:
    """Bản đang hiệu lực của bộ (sẽ bị thay khi phát hành, nếu bản mới mới hơn)."""
    return conn.execute("SELECT doc_id, revision, issue_date, title FROM documents WHERE doc_code = %s AND is_latest",
                        (job["series_doc_code"],)).fetchone()


def list_jobs(conn, status: str | None = None, project_id: str | None = None, limit: int = 200) -> list[dict]:
    cond, args = [], []
    if status:
        cond.append("status = ANY(%s)")
        args.append([s.strip().upper() for s in status.split(",")])
    if project_id:
        cond.append("project_id = %s")
        args.append(project_id)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    # Chỉ lọc job đang chạy/chờ → theo thứ tự hàng chờ (đang xử lý trước, rồi job cũ nhất – cái worker nhận kế tiếp);
    # còn lại → mới nhất trước.
    queue_only = bool(status) and set(args[0]) <= {"QUEUED", "PROCESSING", "PUBLISHING"}
    order = "(status = 'QUEUED'), created_at" if queue_only else "created_at DESC"
    return [dict(r) for r in conn.execute(
        "SELECT job_id, file_name, size, project_id, building, stream, disciplines, doc_type, revision, issue_date, "
        f"series_doc_code, status, step, error, doc_id, uploaded_by, created_at, published_by, published_at, updated_at "
        f"FROM ingest_jobs {where} ORDER BY {order} LIMIT {int(limit)}", args)]


def count_jobs(conn, project_id: str | None = None) -> dict[str, int]:
    """Số job theo trạng thái (toàn bộ, không giới hạn như list_jobs) – cho các nút lọc ở Hàng chờ xử lý."""
    where, args = ("WHERE project_id = %s", [project_id]) if project_id else ("", [])
    return {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, count(*) AS n FROM ingest_jobs {where} GROUP BY status", args)}


def transition(conn, job_id: str, action: str, user_id: str | None = None) -> dict[str, Any]:
    """publish (DRAFT → PUBLISHING, chờ worker) / cancel (→ CANCELLED, xóa artifact; job đang chạy: worker tự dừng)
    / retry (FAILED → QUEUED)."""
    with conn.transaction():
        row = conn.execute("SELECT status, step, artifact_dir FROM ingest_jobs WHERE job_id = %s FOR UPDATE",
                           (job_id,)).fetchone()
        if not row:
            raise KnowledgeError(f"không có job {job_id}", 404)
        new = check_transition(row["status"], action)
        if action == "cancel" and row["status"] == "PUBLISHING" and row["step"] != PUBLISH_QUEUED:
            raise KnowledgeError("job đang ghi vào kho tìm kiếm – không hủy được, chờ xong rồi gỡ phiên bản", 409)
        sets = {"publish": "step = %(q)s, error = NULL, published_by = %(u)s",
                "cancel": "step = NULL",
                "retry": "step = NULL, error = NULL, report = NULL, warnings = '{}', artifact_dir = NULL"}[action]
        conn.execute(f"UPDATE ingest_jobs SET status = %(s)s, {sets}, updated_at = now() WHERE job_id = %(id)s",
                     {"s": new, "q": PUBLISH_QUEUED, "u": user_id, "id": job_id})
    if action in ("cancel", "retry") and row["artifact_dir"]:
        remove_artifacts(Path(row["artifact_dir"]))
    return get_job(conn, job_id)


RECLASSIFY_FROM = frozenset({"DRAFT", "FAILED"})


def reclassify(conn, job_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Bước duyệt: sửa loại tài liệu / phân hệ (/ luồng) mà worker nhận diện → xử lý lại (→ QUEUED, xóa nháp cũ).
    Loại tài liệu quyết định cách đọc đề mục nên phải chạy lại; kết quả parse PDF có cache nên nhanh."""
    upd: dict[str, Any] = {}
    if "doc_type" in fields or "disciplines" in fields:
        c = clean_classification(fields)
        upd.update({k: c[k] for k in ("doc_type", "disciplines") if k in fields})
    if "stream" in fields:
        if fields["stream"] not in Stream.__members__:
            raise KnowledgeError(f"luồng không hợp lệ: {fields['stream']}")
        upd["stream"] = fields["stream"]
    if not upd:
        raise KnowledgeError("không có trường nào để sửa")
    with conn.transaction():
        row = conn.execute("SELECT status, artifact_dir FROM ingest_jobs WHERE job_id = %s FOR UPDATE",
                           (job_id,)).fetchone()
        if not row:
            raise KnowledgeError(f"không có job {job_id}", 404)
        if row["status"] not in RECLASSIFY_FROM:
            raise KnowledgeError(f"chỉ sửa phân loại khi job ở trạng thái DRAFT/FAILED (hiện {row['status']})", 409)
        sets = ", ".join(f"{k} = %({k})s" for k in upd)
        conn.execute(f"UPDATE ingest_jobs SET {sets}, status = 'QUEUED', step = NULL, error = NULL, report = NULL, "
                     "warnings = '{}', artifact_dir = NULL, updated_at = now() WHERE job_id = %(id)s",
                     {**upd, "id": job_id})
    if row["artifact_dir"]:
        remove_artifacts(Path(row["artifact_dir"]))
    return get_job(conn, job_id)


def remove_artifacts(artifact_dir: Path) -> None:
    """Xóa thư mục kết quả nháp của job (<artifacts_dir>/<job_id>/<doc_id> → xóa cả <job_id>)."""
    shutil.rmtree(artifact_dir.parent if artifact_dir.parent.name.startswith("job_") else artifact_dir,
                  ignore_errors=True)
