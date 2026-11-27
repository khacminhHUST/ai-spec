"""Trạng thái / ngữ cảnh chạy của Agent Harness + chuyển đổi `messages` hội thoại ⇄ message của agent.

AgentContext: giá trị do CODE đặt từ request (không bao giờ lấy từ tham số tool LLM truyền vào).
HarnessState: state vòng lặp agent (namespace riêng, không đẩy vào `messages` hiển thị).
"""
from __future__ import annotations

import re
import threading
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
    active_projects: list[str] = field(default_factory=list)   # dự án đang chọn đầu lượt
    history: list[dict] = field(default_factory=list)           # `messages` hội thoại trước lượt này
    sources: list[dict] = field(default_factory=list)           # danh mục nguồn ngoài đầu lượt (mcp/catalog.py)
    open_sources: list[str] = field(default_factory=list)       # nguồn đã mở ở các lượt trước
    next_source_n: int = 1                                      # số [n] kế tiếp trong lượt (tool chạy song song)
    loop_running: bool = False                                  # LimitsMiddleware đánh dấu: False ở lần chạy đầu sau
                                                                #   resume → không tính thời gian chờ xác nhận
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def allocate_sources(self, count: int, existing: int) -> int:
        """Cấp `count` số [n] liên tiếp, không trùng kể cả khi nhiều tool chạy song song. → số đầu tiên.

        existing = số nguồn đã có trong state (sau khi resume, context được tạo lại nhưng state vẫn giữ nguồn cũ).
        """
        with self._lock:
            first = max(self.next_source_n, existing + 1)
            self.next_source_n = first + count
            return first


def runtime_context(runtime: Any) -> AgentContext:
    """AgentContext từ ToolRuntime (context) – thiếu → lỗi rõ ràng, không đoán."""
    ctx = getattr(runtime, "context", None)
    if isinstance(ctx, AgentContext):
        return ctx
    if isinstance(ctx, dict) and ctx.get("user_id") and ctx.get("conversation_id"):
        return AgentContext(ctx["user_id"], ctx["conversation_id"], list(ctx.get("active_projects") or []),
                            list(ctx.get("history") or []), list(ctx.get("sources") or []),
                            list(ctx.get("open_sources") or []))
    raise RuntimeError("Thiếu AgentContext (user_id, conversation_id) trong runtime")


def add_sources(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Reducer `sources`: cộng dồn citation (n toàn cục trong lượt); right = None → reset."""
    if right is None:
        return []
    return [*(left or []), *right]


def add_names(left: list[str] | None, right: list[str] | None) -> list[str]:
    """Reducer `open_sources`: hợp, giữ thứ tự mở (nhiều tool mở nguồn chạy song song)."""
    out = list(left or [])
    out += [x for x in right or [] if x not in out]
    return out


def last_value(left: Any, right: Any) -> Any:
    """Reducer lấy giá trị sau cùng (nhiều tool song song cùng cập nhật không bị lỗi)."""
    return right


class HarnessState(AgentState):
    sources: NotRequired[Annotated[list[dict], add_sources]]     # citation của mọi search trong lượt
    active_projects: NotRequired[Annotated[list[str], last_value]]
    open_sources: NotRequired[Annotated[list[str], add_names]]  # nguồn ngoài đã mở (tool của nguồn được nạp)
    step_count: NotRequired[Annotated[int, PrivateStateAttr]]
    started_at: NotRequired[Annotated[float, PrivateStateAttr]]
    stop_reason: NotRequired[str | None]
    # ngân sách lượt (loop/limits.py): đếm trong after_model, quyết định trong before_model
    force_answer: NotRequired[Annotated[str | None, PrivateStateAttr]]   # lý do ép bước trả lời (không tool)
    search_rounds: NotRequired[Annotated[int, PrivateStateAttr]]         # số bước LLM có gọi search_project_docs
    active_s: NotRequired[Annotated[float, PrivateStateAttr]]            # giây chạy thực (không gồm chờ xác nhận)
    tick: NotRequired[Annotated[float, PrivateStateAttr]]                # thời điểm đo active_s lần cuối
    search_closed: NotRequired[Annotated[str | None, PrivateStateAttr]]  # hết lượt tra (lý do)
    search_closed_at: NotRequired[Annotated[int | None, PrivateStateAttr]]  # step_count lúc đóng tra cứu


# ------------------------------------------------------------------ messages hội thoại ⇄ agent

_FILES_NOTE_RE = re.compile(r"\s*\[File đã tạo:[^\]]*\]")


def files_note(files: list[dict]) -> str:
    return "\n[File đã tạo: " + ", ".join(f"output/{f['name']}" for f in files) + "]" if files else ""


def strip_files_note(text: str) -> str:
    return _FILES_NOTE_RE.sub("", text)


def history_to_messages(history: list[dict]) -> list[BaseMessage]:
    """`messages` hội thoại (dict) → message LangChain cho agent (chỉ text + ghi chú file, không tool calls)."""
    out: list[BaseMessage] = []
    for m in history:
        content = str(m.get("content") or "")
        if m.get("role") == "user":
            out.append(HumanMessage(content))
        elif m.get("role") == "assistant":
            out.append(AIMessage(content + files_note(m.get("files") or [])))
    return out


def turn_messages(user_text: str, answer: str, *, status: str | None = None,
                  citations: list[dict] | None = None, files: list[dict] | None = None,
                  attachments: list[str] | None = None) -> list[dict]:
    """1 lượt (user + assistant) ở định dạng `messages` của Query Pipeline (+ files, attachments)."""
    user: dict[str, Any] = {"role": "user", "content": user_text}
    if attachments:
        user["attachments"] = list(attachments)
    return [user, {"role": "assistant", "content": answer, "status": status, "citations": citations or [],
                   "files": files or []}]
