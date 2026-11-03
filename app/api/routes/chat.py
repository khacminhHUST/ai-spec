"""POST /chat (SSE): status · token · token_reset · tool_start · tool_end · citations · file · confirm_required · done (lỗi → error)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import check_owner, get_deps, get_graph, get_runtime, sse_response
from app.models.chat import ChatRequest
from app.services.agent.graph import stream_assistant_turn
from app.services.agent.runtime import AgentRuntime
from app.services.agent.sandbox import INPUT_DIR
from app.services.query.deps import Deps

router = APIRouter(tags=["chat"])


def _check_attachments(runtime: AgentRuntime, req: ChatRequest) -> None:
    if not req.attachments:
        return
    sbx = runtime.manager.peek(req.conversation_id) if req.conversation_id else None
    have = {f.path.rsplit("/", 1)[-1] for f in sbx.list(INPUT_DIR)} if sbx else set()
    missing = [a for a in req.attachments if a not in have]
    if missing:
        raise HTTPException(400, f"file đính kèm không còn trong sandbox: {', '.join(missing)}")


@router.post("/chat")
def chat(req: ChatRequest, deps: Deps = Depends(get_deps), graph=Depends(get_graph),
         runtime: AgentRuntime = Depends(get_runtime)):
    if req.conversation_id:
        check_owner(deps, req.conversation_id, req.user_id)
    _check_attachments(runtime, req)
    return sse_response(stream_assistant_turn(graph, req.user_id, req.question, req.conversation_id,
                                              req.attachments))
