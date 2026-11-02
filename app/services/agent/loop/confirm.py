"""Tool `write` (ghi/đẩy dữ liệu ra hệ thống ngoài) → LangGraph interrupt → người dùng xác nhận → chạy hoặc bỏ.

Payload interrupt: {"type": "confirm_required", "tool", "args"}; confirm_id = id của Interrupt.
Resume: Command(resume={confirm_id: {"approve": bool}}) (xem resume_command).
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from app.services.agent.tools.base import fail

REJECTED = "Người dùng TỪ CHỐI thực thi – thao tác KHÔNG được thực hiện. Báo lại người dùng, không gọi lại tool này."


def is_approved(decision: Any) -> bool:
    if isinstance(decision, dict):
        return decision.get("approve") is True
    return decision is True


def pending_confirms(interrupts: Any) -> list[dict[str, Any]]:
    """state.interrupts / result["__interrupt__"] → sự kiện confirm_required {confirm_id, tool, args}."""
    out = []
    for it in interrupts or []:
        v = getattr(it, "value", None)
        if isinstance(v, dict) and v.get("type") == "confirm_required":
            out.append({"confirm_id": it.id, "tool": v["tool"], "args": v.get("args") or {}})
    return out


def resume_command(confirm_id: str, approve: bool) -> Command:
    return Command(resume={confirm_id: {"approve": bool(approve)}})


class ConfirmMiddleware(AgentMiddleware):
    def __init__(self, write_tools: set[str]):
        super().__init__()
        self.write_tools = set(write_tools)

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if call["name"] not in self.write_tools:
            return handler(request)
        decision = interrupt({"type": "confirm_required", "tool": call["name"], "args": call.get("args") or {},
                              "tool_call_id": call.get("id")})
        if is_approved(decision):
            return handler(request)
        return ToolMessage(fail(REJECTED, rejected=True), tool_call_id=call["id"], name=call["name"])
