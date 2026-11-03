"""Worker xử lý nền ingest_jobs (tiến trình riêng: python -m app.cli.worker). Mỗi lần 1 job.

- Nhận job bằng SELECT … FOR UPDATE SKIP LOCKED: QUEUED → PROCESSING; PUBLISHING (step=queued) → step=embed.
- PROCESSING: bước 1–5 ingestion (process) với metadata ghi đè từ form → artifact nháp → DRAFT. Không ghi DB tìm kiếm.
- PUBLISHING: đọc lại nháp → save_document (embed + Postgres + Qdrant, versioning theo doc_code) → PUBLISHED.
- Lỗi → FAILED + thông báo ngắn (traceback ghi log). Khởi động: job dở dang của lần trước → FAILED.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.db.client import pg_connect
from app.services.knowledge.config import artifacts_dir, knowledge_cfg
from app.services.knowledge.drafts import load_draft, save_draft
from app.services.knowledge.jobs import PUBLISH_QUEUED
from app.services.knowledge.storage import FileStore, LocalFileStore

log = logging.getLogger(__name__)
def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


INTERRUPTED = "bị gián đoạn (worker dừng giữa chừng) – bấm Chạy lại"


def recover_interrupted(conn) -> int:
    """Job PROCESSING / PUBLISHING đang chạy dở của lần trước → FAILED."""
    cur = conn.execute(
        "UPDATE ingest_jobs SET status = 'FAILED', error = %s, updated_at = now() "
        "WHERE status = 'PROCESSING' OR (status = 'PUBLISHING' AND step IS DISTINCT FROM %s)",
        (INTERRUPTED, PUBLISH_QUEUED))
    return cur.rowcount


def claim_next(conn) -> dict[str, Any] | None:
    """Nhận 1 job (ưu tiên phát hành – nhẹ hơn parse), SKIP LOCKED để nhiều worker không nhận trùng."""
    with conn.transaction():
        row = conn.execute(
            "SELECT * FROM ingest_jobs WHERE status = 'QUEUED' OR (status = 'PUBLISHING' AND step = %s) "
            "ORDER BY (status = 'PUBLISHING') DESC, created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
            (PUBLISH_QUEUED,)).fetchone()
        if not row:
            return None
        status, step = ("PROCESSING", "parse") if row["status"] == "QUEUED" else ("PUBLISHING", "embed")
        conn.execute("UPDATE ingest_jobs SET status = %s, step = %s, error = NULL, updated_at = now() "
                     "WHERE job_id = %s", (status, step, row["job_id"]))
    return {**row, "status": status, "step": step}


def _update(conn, job_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    conn.execute(f"UPDATE ingest_jobs SET {sets}, updated_at = now() WHERE job_id = %(job_id)s",
                 {**fields, "job_id": job_id})


def job_overrides(conn, job: dict[str, Any]) -> dict[str, Any]:
    """Metadata ghi đè cho ingestion (trường để trống → pipeline tự suy từ bìa)."""
    p = conn.execute("SELECT name, aliases FROM projects WHERE project_id = %s", (job["project_id"],)).fetchone()
    name = ((p["aliases"] or [p["name"]])[0]) if p else None
    return {"project_id": job["project_id"], "project_name": name, "building": job["building"],
            "stream": job["stream"], "doc_type": job["doc_type"], "disciplines": list(job["disciplines"]),
            "doc_code": job["series_doc_code"], "title": job["title"], "issuer": job["issuer"],
            "revision": job["revision"], "issue_date": job["issue_date"], "stage": job["stage"]}


def process_job(s: Settings, conn, job: dict[str, Any], store: FileStore, llm=None,
                out_root: Path | None = None) -> None:
    from app.services.ingestion.pipeline import process

    path = store.local_path(job["storage_key"])
    res = process(path, settings=s, llm=llm, overrides=job_overrides(conn, job),
                  on_step=lambda step: _update(conn, job["job_id"], step=step))
    art = save_draft(res, (out_root or artifacts_dir()) / job["job_id"])
    _update(conn, job["job_id"], status="DRAFT", step=None, artifact_dir=str(art),
            report=Jsonb(res.report, dumps=_dumps), warnings=list(res.report["warnings"]))


def publish_job(s: Settings, conn, job: dict[str, Any]) -> None:
    from app.repositories.index_store import save_document

    draft = load_draft(Path(job["artifact_dir"]))
    _update(conn, job["job_id"], step="save")
    saved = save_document(draft.document, draft.parents, draft.children, s)
    if saved["status"] != "inserted":
        raise RuntimeError(f"không phát hành: {saved.get('reason')} ({saved.get('doc_id')})")
    _update(conn, job["job_id"], status="PUBLISHED", step=None, doc_id=draft.document.doc_id,
            published_at=draft.document.ingested_at)


def run_once(s: Settings, store: FileStore | None = None, llm=None, out_root: Path | None = None) -> str | None:
    """Xử lý tối đa 1 job → job_id (None nếu hàng chờ rỗng)."""
    store = store or LocalFileStore()
    with pg_connect(s, autocommit=True) as conn:
        job = claim_next(conn)
        if not job:
            return None
        try:
            if job["status"] == "PROCESSING":
                process_job(s, conn, job, store, llm, out_root)
            else:
                publish_job(s, conn, job)
        except Exception as e:  # noqa: BLE001
            log.exception("Job %s lỗi", job["job_id"])
            _update(conn, job["job_id"], status="FAILED", error=f"{type(e).__name__}: {e}"[:500])
        return job["job_id"]


def run_forever(s: Settings, store: FileStore | None = None, poll_interval_s: float | None = None) -> None:
    interval = float(poll_interval_s or knowledge_cfg()["worker"]["poll_interval_s"])
    with pg_connect(s, autocommit=True) as conn:
        n = recover_interrupted(conn)
    if n:
        log.warning("%d job dở dang → FAILED", n)
    log.info("Worker chạy (ns=%s, poll %.1fs)", s.pg_schema, interval)
    while True:
        if run_once(s, store) is None:
            time.sleep(interval)
