"""Giới hạn vòng lặp: max_steps (số lần gọi LLM), max_consecutive_tool_errors, max_wall_time_s.

Chạm giới hạn → dừng trước lần gọi LLM tiếp theo, trả phần đã làm + lý do (stop_reason).
"""
from __future__ import annotations

import time
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage

from app.services.agent.state import HarnessState


def consecutive_tool_errors(messages: list) -> int:
    """Số ToolMessage lỗi liên tiếp tính từ cuối (bỏ qua AIMessage xen giữa; gặp tool thành công → dừng)."""
    n = 0
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            if m.status != "error":
                break
            n += 1
        elif not isinstance(m, AIMessage):
            break
    return n


def last_ai_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls and m.text.strip():
            return m.text.strip()
    return ""


class LimitsMiddleware(AgentMiddleware):
    state_schema = HarnessState

    def __init__(self, max_steps: int, max_consecutive_tool_errors: int, max_wall_time_s: float,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self.max_steps = max_steps
        self.max_errors = max_consecutive_tool_errors
        self.max_wall = max_wall_time_s
        self.clock = clock

    def before_agent(self, state: HarnessState, runtime: Any) -> dict[str, Any]:
        return {"step_count": 0, "started_at": self.clock(), "stop_reason": None}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: HarnessState, runtime: Any) -> dict[str, Any] | None:
        msgs = state.get("messages") or []
        started = state.get("started_at")
        reason = None
        if int(state.get("step_count") or 0) >= self.max_steps:
            reason = f"đã đạt giới hạn {self.max_steps} bước xử lý"
        elif consecutive_tool_errors(msgs) >= self.max_errors:
            reason = f"công cụ lỗi {self.max_errors} lần liên tiếp"
        elif started is not None and self.clock() - float(started) > self.max_wall:
            reason = f"vượt thời gian tối đa {int(self.max_wall)} giây"
        if reason is None:
            return None
        done = last_ai_text(msgs)
        text = f"Tôi phải dừng vì {reason}." + (f"\n\nPhần đã làm:\n{done}" if done else "") + \
            "\n\nCác file đã tạo (nếu có) được gửi kèm. Bạn có thể yêu cầu làm tiếp hoặc chia nhỏ yêu cầu."
        return {"messages": [AIMessage(text)], "stop_reason": reason, "jump_to": "end"}

    def after_model(self, state: HarnessState, runtime: Any) -> dict[str, Any]:
        return {"step_count": int(state.get("step_count") or 0) + 1}
