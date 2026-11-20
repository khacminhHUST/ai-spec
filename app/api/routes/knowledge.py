"""Kho tri thức (docs/knowledge-admin.md mục 2, 3, 5): upload → nháp → duyệt → phát hành, quản lý phiên bản.

Chưa có đăng nhập: user_id do client gửi, chỉ ghi lại để truy vết (uploaded_by, published_by).
Xử lý nặng (parse, embed) chạy ở worker riêng: python -m app.cli.worker.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import get_deps
from app.api.routes.projects import call, get_conn
from app.services.knowledge import drafts, edits, inspect, jobs, versions
from app.services.knowledge.cache import clear_profiles
from app.services.knowledge.config import max_upload_bytes, options
from app.services.knowledge.storage import FileStore, LocalFileStore, is_pdf, spool_upload
from app.services.query.deps import Deps

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def get_store() -> FileStore:
    return LocalFileStore()


class UserBody(BaseModel):
    user_id: str | None = None


class LatestBody(BaseModel):
    doc_id: str
    user_id: str | None = None


# ---------------------------------------------------------------- danh mục / gợi ý

@router.get("/options")
def get_options():
    return options()


@router.get("/suggest")
def suggest(file_name: str):
    """Gợi ý revision / ngày ban hành / giai đoạn từ tên file (như ingestion bước 1)."""
    from app.services.ingestion.metadata import issue_date_from, revision_from, stage_from

    rev, _ = revision_from(file_name, "", [])
    d, _ = issue_date_from(file_name, "")
    return {"revision": rev, "issue_date": d, "stage": stage_from(file_name)}


# ---------------------------------------------------------------- cây kho / phiên bản

@router.get("/tree")
def get_tree(project_id: str | None = None, conn=Depends(get_conn)):
    return versions.tree(conn, project_id)


@router.get("/series/{doc_code}")
def get_series(doc_code: str, date_from: date | None = Query(None, alias="from"),
               date_to: date | None = Query(None, alias="to"), stage: str | None = None, conn=Depends(get_conn)):
    return call(versions.series_history, conn, doc_code, date_from, date_to, stage)


@router.post("/series/{doc_code}/latest")
def set_latest(doc_code: str, body: LatestBody, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(versions.set_latest, conn, deps.settings, doc_code, body.doc_id)
    clear_profiles(deps)
    return out


@router.post("/series/{doc_code}/unpublish")
def unpublish(doc_code: str, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(versions.unpublish, conn, deps.settings, doc_code)
    clear_profiles(deps)
    return out


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(versions.delete_revision, conn, deps.settings, doc_id)
    clear_profiles(deps)
    return out


class DocumentPatch(BaseModel):
    """Metadata riêng của 1 revision (chỉ gửi trường cần sửa)."""
    title: str | None = None
    issuer: str | None = None
    revision: str | None = None
    issue_date: date | None = None
    stage: str | None = None


class SeriesPatch(BaseModel):
    """Thông tin chung của bộ, áp cho mọi revision (chỉ gửi trường cần sửa)."""
    project_id: str | None = None
    building: str | None = None
    stream: str | None = None
    disciplines: list[str] | None = None


@router.patch("/documents/{doc_id}")
def update_document(doc_id: str, body: DocumentPatch, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(versions.update_document, conn, deps.settings, doc_id, body.model_dump(exclude_unset=True))
    clear_profiles(deps)
    return out


@router.patch("/series/{doc_code}")
def update_series(doc_code: str, body: SeriesPatch, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(versions.update_series, conn, deps.settings, doc_code, body.model_dump(exclude_unset=True))
    clear_profiles(deps)
    return out


@router.delete("/series/{doc_code}")
def delete_series(doc_code: str, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    out = call(versions.delete_series, conn, deps.settings, doc_code)
    clear_profiles(deps)
    return out


@router.get("/documents/{doc_id}")
def document_detail(doc_id: str, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    """Dữ liệu đã lưu của 1 revision: metadata, thống kê, kiểm tra đồng bộ Postgres ↔ Qdrant."""
    return call(inspect.document_detail, conn, deps.settings, doc_id)


@router.get("/documents/{doc_id}/sections")
def document_sections(doc_id: str, q: str | None = None, conn=Depends(get_conn), deps: Deps = Depends(get_deps)):
    return call(inspect.document_sections, conn, deps.settings, doc_id, q)


@router.get("/documents/{doc_id}/sections/{lang}/{section_key}")
def document_section(doc_id: str, lang: str, section_key: str, conn=Depends(get_conn),
                     deps: Deps = Depends(get_deps)):
    return call(inspect.document_section, conn, deps.settings, doc_id, lang, section_key)


# ---------------------------------------------------------------- sửa tay dữ liệu đã lưu

def require_editor(user_id: str | None = Query(None)) -> str | None:
    """Chỗ duy nhất kiểm tra quyền sửa dữ liệu kho. Hiện chưa phân quyền: ai cũng sửa được."""
    return user_id


class SectionPatch(BaseModel):
    number: str | None = None
    title: str | None = None
    breadcrumb: str | None = None
    content_md: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    type: str | None = None


class ChildBody(BaseModel):
    raw_text: str | None = None
    topic: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@router.patch("/documents/{doc_id}/sections/{lang}/{section_key}")
def update_section(doc_id: str, lang: str, section_key: str, body: SectionPatch, conn=Depends(get_conn),
                   deps: Deps = Depends(get_deps), _=Depends(require_editor)):
    return call(edits.update_section, conn, deps.settings, doc_id, lang, section_key,
                body.model_dump(exclude_unset=True))


@router.delete("/documents/{doc_id}/sections/{lang}/{section_key}")
def delete_section(doc_id: str, lang: str, section_key: str, conn=Depends(get_conn),
                   deps: Deps = Depends(get_deps), _=Depends(require_editor)):
    return call(edits.delete_section, conn, deps.settings, doc_id, lang, section_key)


@router.post("/documents/{doc_id}/sections/{lang}/{section_key}/children", status_code=201)
def add_child(doc_id: str, lang: str, section_key: str, body: ChildBody, conn=Depends(get_conn),
              deps: Deps = Depends(get_deps), _=Depends(require_editor)):
    return call(edits.add_child, conn, deps.settings, doc_id, lang, section_key, body.model_dump(exclude_unset=True))


@router.patch("/documents/{doc_id}/children/{lang}/{child_id:path}")
def update_child(doc_id: str, lang: str, child_id: str, body: ChildBody, conn=Depends(get_conn),
                 deps: Deps = Depends(get_deps), _=Depends(require_editor)):
    return call(edits.update_child, conn, deps.settings, doc_id, lang, child_id, body.model_dump(exclude_unset=True))


@router.delete("/documents/{doc_id}/children/{lang}/{child_id:path}")
def delete_child(doc_id: str, lang: str, child_id: str, conn=Depends(get_conn),
                 deps: Deps = Depends(get_deps), _=Depends(require_editor)):
    return call(edits.delete_child, conn, deps.settings, doc_id, lang, child_id)


@router.get("/documents/{doc_id}/file")
def download(doc_id: str, download: bool = False, conn=Depends(get_conn)):
    row = call(versions.source_file, conn, doc_id)
    path = Path(row["source_path"])
    if not path.is_file():
        raise HTTPException(404, "không còn file gốc")
    # mặc định inline (xem trực tiếp trong UI); ?download=1 → tải xuống
    return FileResponse(path, media_type="application/pdf", filename=row["file_name"] or path.name,
                        content_disposition_type="attachment" if download else "inline")


# ---------------------------------------------------------------- upload / job

@router.post("/uploads", status_code=201)
def upload(file: UploadFile = File(...), metadata: str = Form(...), user_id: str = Form(...),
           conn=Depends(get_conn), store: FileStore = Depends(get_store)):
    name = Path(file.filename or "").name
    if not name.lower().endswith(".pdf"):
        raise HTTPException(415, "chỉ nhận file PDF (parse hiện chỉ hỗ trợ PDF)")
    try:
        md = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(400, "metadata phải là JSON") from None
    try:
        tmp, file_hash, size = spool_upload(file.file, max_upload_bytes())
    except ValueError as e:
        raise HTTPException(413, str(e)) from None
    try:
        if not is_pdf(tmp):
            raise HTTPException(415, "file không phải PDF hợp lệ")
        return call(jobs.create_job, conn, store, file_name=name, src=tmp, file_hash=file_hash, size=size,
                    metadata=md, user_id=user_id)
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/jobs")
def list_jobs(status: str | None = None, project_id: str | None = None, conn=Depends(get_conn)):
    return jobs.list_jobs(conn, status, project_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, conn=Depends(get_conn)):
    job = call(jobs.get_job, conn, job_id)
    job["replaces"] = jobs.replaced_doc(conn, job)
    return job


class JobPatch(BaseModel):
    """Bước duyệt: sửa phân loại worker đã nhận diện (chỉ gửi trường cần sửa) → job xử lý lại."""
    doc_type: str | None = None
    disciplines: list[str] | None = None
    stream: str | None = None


@router.patch("/jobs/{job_id}")
def reclassify_job(job_id: str, body: JobPatch, conn=Depends(get_conn)):
    return call(jobs.reclassify, conn, job_id, body.model_dump(exclude_none=True))


def _artifact_dir(conn, job_id: str) -> Path:
    job = call(jobs.get_job, conn, job_id)
    if not job["artifact_dir"] or not Path(job["artifact_dir"]).is_dir():
        raise HTTPException(409, f"job {job_id} chưa có kết quả nháp ({job['status']})")
    return Path(job["artifact_dir"])


@router.get("/jobs/{job_id}/sections")
def job_sections(job_id: str, conn=Depends(get_conn)):
    return drafts.sections(_artifact_dir(conn, job_id))


@router.get("/jobs/{job_id}/sections/{lang}/{section_key}")
def job_section(job_id: str, lang: str, section_key: str, conn=Depends(get_conn)):
    out = drafts.section_preview(_artifact_dir(conn, job_id), lang, section_key)
    if out is None:
        raise HTTPException(404, f"không có mục {lang}/{section_key}")
    return out


@router.post("/jobs/{job_id}/{action}")
def job_action(job_id: str, action: str, body: UserBody | None = None, conn=Depends(get_conn)):
    if action not in jobs.ACTIONS:
        raise HTTPException(404, f"thao tác không hỗ trợ: {action}")
    return call(jobs.transition, conn, job_id, action, body.user_id if body else None)
