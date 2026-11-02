"""Dependency FastAPI: lấy graph/deps đã khởi tạo trong lifespan (app.state)."""
from __future__ import annotations

import json
from typing import Any, Iterable

from fastapi import HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from app.services.agent.runtime import AgentRuntime
from app.services.query.deps import Deps


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def get_graph(request: Request):
    return request.app.state.graph


def get_runtime(request: Request) -> AgentRuntime:
    return request.app.state.runtime


def check_owner(deps: Deps, conversation_id: str, user_id: str, *, must_exist: bool = False) -> dict[str, Any] | None:
    """Hội thoại của user khác → 403; must_exist mà không có → 404."""
    conv = deps.repo.get_conversation(conversation_id)
    if conv and conv["user_id"] != user_id:
        raise HTTPException(403, "conversation thuộc user khác")
    if must_exist and not conv:
        raise HTTPException(404, "không tìm thấy hội thoại")
    return conv


def sse_response(stream: Iterable[dict]) -> EventSourceResponse:
    """Sự kiện {event, data} → SSE; lỗi trong lúc chạy → sự kiện error."""

    def events():
        try:
            for ev in stream:
                data = ev["data"]
                yield {"event": ev["event"],
                       "data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)}
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(iterate_in_threadpool(events()))
