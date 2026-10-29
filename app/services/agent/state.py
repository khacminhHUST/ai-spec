"""Trạng thái / ngữ cảnh chạy của Agent Harness.

AgentContext: giá trị do CODE đặt từ request (không bao giờ lấy từ tham số tool LLM truyền vào).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    user_id: str
    conversation_id: str
    active_projects: list[str] = field(default_factory=list)   # đầu lượt (dùng chung với lookup)
    history: list[dict] = field(default_factory=list)           # `messages` hợp nhất trước lượt này (định dạng lookup)


def runtime_context(runtime: Any) -> AgentContext:
    """AgentContext từ ToolRuntime (context) – thiếu → lỗi rõ ràng, không đoán."""
    ctx = getattr(runtime, "context", None)
    if isinstance(ctx, AgentContext):
        return ctx
    if isinstance(ctx, dict) and ctx.get("user_id") and ctx.get("conversation_id"):
        return AgentContext(ctx["user_id"], ctx["conversation_id"], list(ctx.get("active_projects") or []),
                            list(ctx.get("history") or []))
    raise RuntimeError("Thiếu AgentContext (user_id, conversation_id) trong runtime")
