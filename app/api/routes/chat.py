"""POST /chat (SSE): status · token · citations · done (lỗi → error)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from app.api.deps import get_deps, get_graph
from app.models.chat import ChatRequest
from app.services.query.deps import Deps
from app.services.query.graph import stream_turn

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(req: ChatRequest, deps: Deps = Depends(get_deps), graph=Depends(get_graph)):
    if req.conversation_id:
        conv = deps.repo.get_conversation(req.conversation_id)
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
