"""FastAPI app.

    POST /chat (SSE)                {conversation_id?, user_id, question} → status · token · citations · done
    GET  /conversations?user_id=    danh sách hội thoại
    GET  /conversations/{id}        hội thoại + messages (từ checkpoint)

Chạy: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import chat, conversations
from app.services.query.deps import Deps
from app.services.query.graph import build_graph


def create_app(deps: Deps | None = None, checkpointer=None) -> FastAPI:
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
        app.state.deps = d
        app.state.graph = build_graph(d, cp)
        try:
            yield
        finally:
            if conn is not None:
                conn.close()

    app = FastAPI(title="MEP Project Assistant", lifespan=lifespan)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    return app


app = create_app()
