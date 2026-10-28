"""GET /conversations?user_id= · GET /conversations/{id} (kèm messages từ checkpoint)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_deps, get_graph
from app.services.query.deps import Deps
from app.services.query.graph import turn_config

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_conversations(user_id: str = Query(...), deps: Deps = Depends(get_deps)) -> list[dict[str, Any]]:
    return deps.repo.list_conversations(user_id)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, deps: Deps = Depends(get_deps), graph=Depends(get_graph)) -> dict[str, Any]:
    conv = deps.repo.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "không tìm thấy hội thoại")
    values = graph.get_state(turn_config(conversation_id)).values
    return {**conv, "messages": values.get("messages") or [], "active_projects": values.get("active_projects") or []}
