"""POST /chat/confirm (SSE): đồng ý / từ chối tool write đang chờ, tiếp tục lượt."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import check_owner, get_deps, get_graph, sse_response
from app.models.agent import ConfirmRequest
from app.services.agent.graph import pending_confirm_ids, stream_confirm
from app.services.query.deps import Deps

router = APIRouter(tags=["chat"])


@router.post("/chat/confirm")
def confirm(req: ConfirmRequest, deps: Deps = Depends(get_deps), graph=Depends(get_graph)):
    check_owner(deps, req.conversation_id, req.user_id, must_exist=True)
    if req.confirm_id not in pending_confirm_ids(graph, req.conversation_id):
        raise HTTPException(409, "không có xác nhận đang chờ với confirm_id này")
    return sse_response(stream_confirm(graph, req.conversation_id, req.confirm_id, req.approve))
