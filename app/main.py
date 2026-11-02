"""FastAPI app.

    POST /chat (SSE)                {conversation_id?, user_id, question, attachments?, force_route?}
    POST /chat/confirm (SSE)        {conversation_id, user_id, confirm_id, approve}
    GET  /conversations?user_id=    danh sách hội thoại
    GET  /conversations/{id}        hội thoại + messages (từ checkpoint)
    POST /conversations/{id}/files  upload file vào sandbox
    GET  /conversations/{id}/files/{name}  tải file kết quả

Chạy: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import chat, confirm, conversations, files
from app.services.agent.graph import build_assistant_graph
from app.services.agent.runtime import AgentRuntime, build_runtime
from app.services.query.deps import Deps

log = logging.getLogger(__name__)


def _destroy_orphan_sandboxes(backend: str) -> None:
    """Sandbox của lần chạy trước không còn được quản lý (không tự hủy theo TTL) → dọn khi khởi động."""
    if backend != "docker":
        return
    try:
        from app.services.agent.sandbox.docker_backend import DockerSandbox

        DockerSandbox.destroy_all()
    except Exception as e:  # noqa: BLE001 – Docker chưa chạy: tuyến lookup vẫn hoạt động
        log.warning("Không dọn được sandbox cũ: %s", e)


def create_app(deps: Deps | None = None, checkpointer=None, runtime: AgentRuntime | None = None) -> FastAPI:
    """deps/checkpointer = None → bản thật (Postgres pool + PostgresSaver qua app.db.client.checkpointer_conn)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = None
        d = deps or Deps.default()
        cp = checkpointer
        if cp is None:
            from langgraph.checkpoint.postgres import PostgresSaver

            from app.db.client import checkpointer_conn

            conn = checkpointer_conn(d.settings)
            cp = PostgresSaver(conn)
        rt = runtime or build_runtime(d)
        if deps is None:
            await asyncio.to_thread(_destroy_orphan_sandboxes, rt.cfg["sandbox"].get("backend", "docker"))
        app.state.deps = d
        app.state.runtime = rt
        app.state.graph = build_assistant_graph(d, rt, cp)

        async def reap_loop():
            while True:
                await asyncio.sleep(float(rt.cfg["sandbox"]["reap_interval_s"]))
                try:
                    await asyncio.to_thread(rt.manager.reap_idle)
                except Exception as e:  # noqa: BLE001
                    log.warning("Dọn sandbox idle lỗi: %s", e)

        reaper = asyncio.create_task(reap_loop())
        try:
            yield
        finally:
            reaper.cancel()
            await asyncio.to_thread(rt.manager.close_all)
            if conn is not None:
                conn.close()

    app = FastAPI(title="MEP Project Assistant", lifespan=lifespan)
    app.include_router(chat.router)
    app.include_router(confirm.router)
    app.include_router(conversations.router)
    app.include_router(files.router)
    return app


app = create_app()
