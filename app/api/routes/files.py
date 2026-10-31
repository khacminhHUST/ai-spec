"""POST /conversations/{id}/files (upload vào /workspace/input) · GET /conversations/{id}/files/{name} (tải từ /workspace/output)."""
from __future__ import annotations

import mimetypes
import posixpath
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from app.api.deps import check_owner, get_deps, get_runtime
from app.models.agent import UploadedFile
from app.services.agent.runtime import AgentRuntime
from app.services.agent.sandbox import INPUT_DIR, OUTPUT_DIR, SandboxError, workspace_path
from app.services.query.deps import Deps

router = APIRouter(prefix="/conversations", tags=["files"])


def _max_bytes(runtime: AgentRuntime) -> int:
    return int(runtime.cfg["sandbox"]["max_output_file_mb"]) << 20


@router.post("/{conversation_id}/files", response_model=UploadedFile)
def upload(conversation_id: str, user_id: str = Query(...), file: UploadFile = File(...),
           deps: Deps = Depends(get_deps), runtime: AgentRuntime = Depends(get_runtime)) -> UploadedFile:
    name = posixpath.basename((file.filename or "").replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise HTTPException(400, "tên file không hợp lệ")
    check_owner(deps, conversation_id, user_id)
    limit = _max_bytes(runtime)
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"file vượt giới hạn {runtime.cfg['sandbox']['max_output_file_mb']} MB")
    deps.repo.upsert_conversation(conversation_id, user_id, f"File: {name}")   # giữ quyền sở hữu hội thoại mới
    try:
        runtime.manager.get(conversation_id).put_file(f"{INPUT_DIR}/{name}", data)
    except SandboxError as e:
        raise HTTPException(400, str(e)) from None
    return UploadedFile(name=name, size=len(data))


@router.get("/{conversation_id}/files/{name:path}")
def download(conversation_id: str, name: str, user_id: str = Query(...), deps: Deps = Depends(get_deps),
             runtime: AgentRuntime = Depends(get_runtime)) -> Response:
    check_owner(deps, conversation_id, user_id, must_exist=True)
    try:
        path = workspace_path(f"{OUTPUT_DIR}/{name}")
    except SandboxError as e:
        raise HTTPException(400, str(e)) from None
    if not path.startswith(OUTPUT_DIR + "/"):
        raise HTTPException(400, "chỉ tải được file trong /workspace/output")
    sbx = runtime.manager.peek(conversation_id)
    if sbx is None:
        raise HTTPException(404, "sandbox đã hết hạn – file không còn")
    try:
        data = sbx.get_file(path, max_bytes=_max_bytes(runtime))
    except SandboxError as e:
        raise HTTPException(404, str(e)) from None
    filename = posixpath.basename(path)
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    return Response(data, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    headers={"Content-Disposition": disposition})
