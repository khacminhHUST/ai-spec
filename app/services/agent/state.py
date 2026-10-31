"""Trạng thái / ngữ cảnh chạy của Agent Harness + chuyển đổi messages lookup ⇄ agent.

AgentContext: giá trị do CODE đặt từ request (không bao giờ lấy từ tham số tool LLM truyền vào).
HarnessState: state vòng lặp agent (namespace riêng, không đẩy vào `messages` hiển thị).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

from langchain.agents import AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from typing_extensions import NotRequired


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


def add_sources(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Reducer `sources`: cộng dồn citation (n toàn cục trong lượt); right = None → reset."""
    if right is None:
        return []
    return [*(left or []), *right]


class HarnessState(AgentState):
    sources: NotRequired[Annotated[list[dict], add_sources]]     # citation của mọi search trong lượt
    active_projects: NotRequired[list[str]]
    step_count: NotRequired[Annotated[int, PrivateStateAttr]]
    started_at: NotRequired[Annotated[float, PrivateStateAttr]]
    stop_reason: NotRequired[str | None]


# ------------------------------------------------------------------ messages lookup ⇄ agent

def files_note(files: list[dict]) -> str:
    return "\n[File đã tạo: " + ", ".join(f"output/{f['name']}" for f in files) + "]" if files else ""


def history_to_messages(history: list[dict]) -> list[BaseMessage]:
    """`messages` hợp nhất (dict) → message LangChain cho agent (chỉ text + ghi chú file, không tool calls)."""
    out: list[BaseMessage] = []
    for m in history:
        content = str(m.get("content") or "")
        if m.get("role") == "user":
            out.append(HumanMessage(content))
        elif m.get("role") == "assistant":
            out.append(AIMessage(content + files_note(m.get("files") or [])))
    return out


def turn_messages(user_text: str, answer: str, *, route: str, status: str | None = None,
                  citations: list[dict] | None = None, files: list[dict] | None = None,
                  attachments: list[str] | None = None) -> list[dict]:
    """1 lượt (user + assistant) ở định dạng `messages` của Query Pipeline (+ route, files, attachments)."""
    user: dict[str, Any] = {"role": "user", "content": user_text}
    if attachments:
        user["attachments"] = list(attachments)
    return [user, {"role": "assistant", "content": answer, "status": status, "citations": citations or [],
                   "route": route, "files": files or []}]
