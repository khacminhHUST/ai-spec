"""Tool `write` (ghi/đẩy dữ liệu ra hệ thống ngoài) → LangGraph interrupt → người dùng xác nhận → chạy hoặc bỏ.

Payload interrupt: {"type": "confirm_required", "tool", "args"}; confirm_id = id của Interrupt.
Resume: Command(resume={confirm_id: {"approve": bool}}) (xem resume_command).
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from app.services.agent.tools.base import access_of, fail

REJECTED = "Người dùng TỪ CHỐI thực thi – thao tác KHÔNG được thực hiện. Báo lại người dùng, không gọi lại tool này."
UNAVAILABLE = ("Tool {name} không có trong bộ tool hiện tại (chưa mở nguồn, hoặc connector đã tắt / ngắt kết nối) – thao tác "
               "KHÔNG được thực hiện. Không gọi lại tool này; dùng tool có sẵn hoặc báo lại người dùng.")


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
    """Đặt sau SourcesMiddleware: tool của nguồn ngoài đã được gắn vào request trước khi xét access."""

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if getattr(request, "tool", None) is None:
            # Tool không có trong agent và không thuộc nguồn đang mở (VD resume sau khi ngắt kết nối)
            return ToolMessage(fail(UNAVAILABLE.format(name=call["name"]), unavailable=True), tool_call_id=call["id"],
                               name=call["name"], status="error")
        if access_of(request.tool) != "write":
            return handler(request)
        decision = interrupt({"type": "confirm_required", "tool": call["name"], "args": call.get("args") or {},
                              "tool_call_id": call.get("id")})
        if is_approved(decision):
            return handler(request)
        return ToolMessage(fail(REJECTED, rejected=True), tool_call_id=call["id"], name=call["name"])
