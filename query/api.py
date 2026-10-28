"""API FastAPI.

    POST /chat (SSE)                {conversation_id?, user_id, question} → status · token · citations · done
    GET  /conversations?user_id=    danh sách hội thoại
    GET  /conversations/{id}        hội thoại + messages (từ checkpoint)

Chạy: uvicorn query.api:app --port 8000
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from query.deps import Deps
from query.graph import build_graph, stream_turn, turn_config


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    user_id: str
    question: str = Field(min_length=1)


def create_app(deps: Deps | None = None, checkpointer=None) -> FastAPI:
    """deps/checkpointer = None → bản thật (Postgres pool + PostgresSaver qua shared.db.checkpointer_conn)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = None
        d = deps or Deps.default()
        cp = checkpointer
        if cp is None:
            from langgraph.checkpoint.postgres import PostgresSaver

            from shared.db import checkpointer_conn

            conn = checkpointer_conn(d.settings)
            cp = PostgresSaver(conn)
        app.state.deps = d
        app.state.graph = build_graph(d, cp)
        try:
            yield
        finally:
            if conn is not None:
                conn.close()

    app = FastAPI(title="MEP Project Assistant – Query", lifespan=lifespan)

    @app.post("/chat")
    async def chat(req: ChatRequest):
        graph = app.state.graph
        if req.conversation_id:
            conv = app.state.deps.repo.get_conversation(req.conversation_id)
            if conv and conv["user_id"] != req.user_id:
                raise HTTPException(403, "conversation thuộc user khác")

        def events():
            try:
                for ev in stream_turn(graph, req.user_id, req.question, req.conversation_id):
                    data = ev["data"]
                    yield {"event": ev["event"], "data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)}
            except Exception as e:  # noqa: BLE001
                yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}

        return EventSourceResponse(iterate_in_threadpool(events()))

    @app.get("/conversations")
    def list_conversations(user_id: str = Query(...)) -> list[dict[str, Any]]:
        return app.state.deps.repo.list_conversations(user_id)

    @app.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> dict[str, Any]:
        conv = app.state.deps.repo.get_conversation(conversation_id)
        if not conv:
            raise HTTPException(404, "không tìm thấy hội thoại")
        values = app.state.graph.get_state(turn_config(conversation_id)).values
        return {**conv, "messages": values.get("messages") or [], "active_projects": values.get("active_projects") or []}

    return app


app = create_app()
