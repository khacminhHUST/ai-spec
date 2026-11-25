"""Worker xử lý nền ingest_jobs (tiến trình riêng: python -m app.cli.worker). Mỗi lần 1 job.

- Nhận job bằng SELECT … FOR UPDATE SKIP LOCKED: QUEUED → PROCESSING; PUBLISHING (step=queued) → step=embed.
- PROCESSING: bước 1–5 ingestion (process) với metadata ghi đè từ form → artifact nháp → DRAFT. Không ghi DB tìm kiếm.
- PUBLISHING: đọc lại nháp → save_document (embed + Postgres + Qdrant, versioning theo doc_code) → PUBLISHED.
- auto_publish (config/knowledge.yaml › worker.auto_publish): xử lý xong phát hành luôn, không dừng ở DRAFT.
- Chạy kèm server (worker.embedded): app/main.py bật run_forever trong 1 thread nền, dừng theo `stop`.
- Lỗi → FAILED + thông báo ngắn (traceback ghi log). Khởi động: job dở dang của lần trước → FAILED.
- Hủy job đang PROCESSING (giao diện → CANCELLED): worker dừng ở bước kế tiếp (on_step) và không ghi đè trạng thái.
  Worker tiến trình riêng (hard_cancel): thread canh hủy thoát hẳn tiến trình để cắt ngang bước parse dài;
  Docker (restart: unless-stopped) tự bật lại worker. Worker nhúng trong server không thoát được → chỉ dừng mềm.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
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
CANCEL_POLL_S = 5.0
CANCEL_EXIT_CODE = 3


class JobCancelled(Exception):
    """Job bị hủy từ giao diện trong lúc worker đang xử lý."""


def is_cancelled(conn, job_id: str) -> bool:
    r = conn.execute("SELECT status FROM ingest_jobs WHERE job_id = %s", (job_id,)).fetchone()
    return r is None or r["status"] == "CANCELLED"


def _check_cancel(conn, job_id: str) -> None:
    if is_cancelled(conn, job_id):
        raise JobCancelled(job_id)


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


def _update(conn, job_id: str, *, only_if: str | None = None, **fields: Any) -> int:
    """only_if: chỉ ghi khi job còn ở trạng thái này (job bị hủy giữa chừng → không ghi đè). → số dòng ghi."""
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    cond = " AND status = %(only_if)s" if only_if else ""
    return conn.execute(f"UPDATE ingest_jobs SET {sets}, updated_at = now() WHERE job_id = %(job_id)s{cond}",
                        {**fields, "job_id": job_id, "only_if": only_if}).rowcount


def job_overrides(conn, job: dict[str, Any]) -> dict[str, Any]:
    """Metadata ghi đè cho ingestion (trường để trống → pipeline tự suy từ bìa)."""
    p = conn.execute("SELECT name, aliases FROM projects WHERE project_id = %s", (job["project_id"],)).fetchone()
    name = ((p["aliases"] or [p["name"]])[0]) if p else None
    return {"project_id": job["project_id"], "project_name": name, "building": job["building"],
            "stream": job["stream"], "doc_type": job["doc_type"], "disciplines": list(job["disciplines"]),
            "doc_code": job["series_doc_code"], "title": job["title"], "issuer": job["issuer"],
            "revision": job["revision"], "issue_date": job["issue_date"], "stage": job["stage"]}


def classify_job(conn, job: dict[str, Any], path: Path, llm=None) -> dict[str, Any] | None:
    """Upload gọn để trống loại tài liệu / phân hệ → nhận diện (classify.py), ghi vào job (sửa được ở bước duyệt).
    Parse PDF ở đây được cache theo file_hash → ingestion dùng lại, không parse 2 lần. Đã đủ → None."""
    if job["doc_type"] and job["disciplines"]:
        return None
    from app.services.ingestion.parse import parse_pdf
    from app.services.knowledge.classify import classify

    _update(conn, job["job_id"], only_if="PROCESSING", step="classify")
    parsed = parse_pdf(path, job["file_hash"])
    got = classify(parsed.blocks, job["file_name"], job["stream"], llm)
    upd = {"doc_type": job["doc_type"] or got["doc_type"], "disciplines": list(job["disciplines"] or got["disciplines"])}
    _update(conn, job["job_id"], **upd)
    job.update(upd)
    return {**got, **upd}


class OcrRejected(RuntimeError):
    """PDF cần OCR quá nhiều trang / OCR quá giờ – báo lỗi rõ thay vì chặn hàng chờ."""


def ocr_pages(path: Path) -> tuple[int, str]:
    """→ (số trang sẽ phải OCR, mô tả). Lỗi font nhiều → Docling OCR toàn trang cả file (docling_cache.convert)."""
    import pypdfium2 as pdfium

    from app.services.ingestion.docling_cache import analyze_pages, needs_full_ocr

    scans, broken, n_text = analyze_pages(path)
    if needs_full_ocr(broken, n_text):
        total = len(pdfium.PdfDocument(str(path)))
        return total, f"lỗi font {len(broken)} trang → OCR toàn bộ {total} trang"
    return len(scans), f"{len(scans)} trang scan"


def prewarm_ocr(conn, job: dict[str, Any], path: Path, max_pages: int, timeout_s: float,
                poll_s: float = CANCEL_POLL_S, cmd: list[str] | None = None) -> dict[str, Any] | None:
    """PDF cần OCR → parse Docling trong TIẾN TRÌNH CON (ghi cache theo file_hash, bước sau đọc cache):
    quá `max_pages` trang OCR → từ chối ngay; quá `timeout_s` → giết tiến trình con, báo lỗi; job bị hủy → giết.
    Lý do: OCR trên CPU ~9 s/trang, vài GB RAM, không ngắt giữa chừng được – trước đây 1 file scan treo cả hàng chờ.
    Không cần OCR → None (parse text nhanh, chạy như cũ)."""
    import subprocess
    import sys
    import tempfile
    import time

    from app.core.config import ROOT

    n, desc = ocr_pages(path)
    if n == 0:
        return None
    if n > max_pages:
        raise OcrRejected(f"PDF cần OCR {n} trang ({desc}) – vượt giới hạn {max_pages} trang/file. "
                          "Tách file hoặc OCR trước rồi tải lên lại.")
    _update(conn, job["job_id"], only_if="PROCESSING", step="ocr")
    log.info("Job %s: OCR %s (tiến trình con, tối đa %.0fs)", job["job_id"], desc, timeout_s)
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as err:   # log Docling ra file (pipe đầy bộ đệm sẽ làm tiến trình con kẹt)
        proc = subprocess.Popen(cmd or [sys.executable, "-m", "app.services.ingestion.docling_cache", str(path)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=err)
        try:
            while proc.poll() is None:
                if time.monotonic() - t0 > timeout_s:
                    raise OcrRejected(f"OCR quá {timeout_s / 60:.0f} phút ({desc}) – đã dừng. "
                                      "Tách file hoặc OCR trước rồi tải lên lại.")
                _check_cancel(conn, job["job_id"])
                time.sleep(poll_s)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace")[-300:]
            raise RuntimeError(f"OCR lỗi (mã {proc.returncode}): {tail}")
    return {"ocr_pages": n, "desc": desc, "seconds": round(time.monotonic() - t0)}


def process_job(s: Settings, conn, job: dict[str, Any], store: FileStore, llm=None,
                out_root: Path | None = None) -> None:
    from app.services.ingestion.pipeline import process

    job_id = job["job_id"]

    def on_step(step: str) -> None:
        _check_cancel(conn, job_id)
        _update(conn, job_id, only_if="PROCESSING", step=step)

    path = store.local_path(job["storage_key"])
    wcfg = knowledge_cfg()["worker"]
    prewarm_ocr(conn, job, path, int(wcfg["ocr_max_pages"]), float(wcfg["ocr_timeout_s"]))
    _check_cancel(conn, job_id)
    cls = classify_job(conn, job, path, llm)
    _check_cancel(conn, job_id)
    res = process(path, settings=s, llm=llm, overrides=job_overrides(conn, job), on_step=on_step)
    if cls:
        res.report["classification"] = cls
    art = save_draft(res, (out_root or artifacts_dir()) / job_id)
    if not _update(conn, job_id, only_if="PROCESSING", status="DRAFT", step=None, artifact_dir=str(art),
                   report=Jsonb(res.report, dumps=_dumps), warnings=list(res.report["warnings"])):
        shutil.rmtree(art, ignore_errors=True)
        raise JobCancelled(job_id)


def publish_job(s: Settings, conn, job: dict[str, Any]) -> None:
    from app.repositories.index_store import save_document

    draft = load_draft(Path(job["artifact_dir"]))
    _update(conn, job["job_id"], step="save")
    saved = save_document(draft.document, draft.parents, draft.children, s)
    if saved["status"] != "inserted":
        raise RuntimeError(f"không phát hành: {saved.get('reason')} ({saved.get('doc_id')})")
    _update(conn, job["job_id"], status="PUBLISHED", step=None, doc_id=draft.document.doc_id,
            published_at=draft.document.ingested_at)


def auto_publish_job(s: Settings, conn, job_id: str) -> None:
    """Nháp vừa xong → phát hành ngay (người upload là người phát hành)."""
    job = dict(conn.execute("SELECT * FROM ingest_jobs WHERE job_id = %s", (job_id,)).fetchone())
    _update(conn, job_id, status="PUBLISHING", step="embed", published_by=job["uploaded_by"])
    publish_job(s, conn, job)


def _watch_cancel(s: Settings, job_id: str, done: threading.Event, art: Path) -> None:
    """Thread canh hủy (chỉ worker tiến trình riêng): job PROCESSING bị hủy → xóa nháp dở, thoát tiến trình
    (bước parse Docling/OCR không ngắt giữa chừng được). Docker tự khởi động lại worker."""
    try:
        with pg_connect(s, autocommit=True) as conn:
            while not done.wait(CANCEL_POLL_S):
                if is_cancelled(conn, job_id):
                    log.warning("Job %s bị hủy khi đang xử lý → thoát worker (Docker sẽ bật lại)", job_id)
                    shutil.rmtree(art, ignore_errors=True)
                    logging.shutdown()
                    os._exit(CANCEL_EXIT_CODE)
    except Exception as e:  # noqa: BLE001 – mất kết nối: bỏ canh, job vẫn dừng mềm ở bước kế tiếp
        log.warning("Canh hủy job %s lỗi: %s", job_id, e)


def run_once(s: Settings, store: FileStore | None = None, llm=None, out_root: Path | None = None,
             auto_publish: bool = False, hard_cancel: bool = False) -> str | None:
    """Xử lý tối đa 1 job → job_id (None nếu hàng chờ rỗng). auto_publish: xong nháp → phát hành luôn.
    hard_cancel: job bị hủy giữa chừng → thoát tiến trình (chỉ dùng cho worker tiến trình riêng)."""
    store = store or LocalFileStore()
    with pg_connect(s, autocommit=True) as conn:
        job = claim_next(conn)
        if not job:
            return None
        job_id = job["job_id"]
        done = threading.Event()
        try:
            if job["status"] == "PROCESSING":
                if hard_cancel:
                    threading.Thread(target=_watch_cancel, args=(s, job_id, done, (out_root or artifacts_dir()) / job_id),
                                     daemon=True, name=f"cancel-{job_id}").start()
                process_job(s, conn, job, store, llm, out_root)
                done.set()
                st = conn.execute("SELECT status FROM ingest_jobs WHERE job_id = %s", (job_id,)).fetchone()
                if auto_publish and st and st["status"] == "DRAFT":
                    auto_publish_job(s, conn, job_id)
            else:
                publish_job(s, conn, job)
        except JobCancelled:
            log.info("Job %s đã hủy – dừng xử lý", job_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Job %s lỗi", job_id)
            if not is_cancelled(conn, job_id):
                _update(conn, job_id, status="FAILED", error=f"{type(e).__name__}: {e}"[:500])
        finally:
            done.set()
        return job_id


def run_forever(s: Settings, store: FileStore | None = None, poll_interval_s: float | None = None,
                stop: threading.Event | None = None, auto_publish: bool | None = None,
                hard_cancel: bool = False) -> None:
    wcfg = knowledge_cfg()["worker"]
    interval = float(poll_interval_s or wcfg["poll_interval_s"])
    auto = bool(wcfg.get("auto_publish")) if auto_publish is None else auto_publish
    stop = stop or threading.Event()
    with pg_connect(s, autocommit=True) as conn:
        n = recover_interrupted(conn)
    if n:
        log.warning("%d job dở dang → FAILED", n)
    log.info("Worker chạy (ns=%s, poll %.1fs, tự phát hành: %s)", s.pg_schema, interval, auto)
    while not stop.is_set():
        try:
            done = run_once(s, store, auto_publish=auto, hard_cancel=hard_cancel)
        except Exception as e:  # noqa: BLE001 – lỗi kết nối DB…: chờ rồi thử lại, không dừng worker
            log.warning("Worker lỗi: %s", e)
            done = None
        if done is None:
            stop.wait(interval)
