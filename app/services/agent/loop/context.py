"""Quản lý ngữ cảnh agent.

- Kết quả tool lớn → ghi file JSON trong sandbox, trả LLM bản tóm tắt + đường dẫn (fit_or_offload, ToolGuardMiddleware).
- Tool lỗi / ném exception → ToolMessage lỗi có cấu trúc (status="error"), vòng lặp không sập.
- Tóm tắt hội thoại > summarize_above_tokens: SummarizationMiddleware (xem loop/agent.py).
"""
from __future__ import annotations

import itertools
import json
import logging
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from app.services.agent.sandbox import INPUT_DIR, SandboxManager
from app.services.agent.state import runtime_context
from app.services.agent.tools.base import clip, fail

log = logging.getLogger(__name__)
_seq = itertools.count(1)
SELF_MANAGED = {"search_project_docs", "read_file", "run_code"}   # tool tự cắt/offload kết quả


def approx_tokens(text: str) -> int:
    """Ước lượng nhanh (≈ 3 ký tự/token cho VN/EN lẫn) khi không cần chính xác."""
    return max(1, len(text) // 3) if text else 0


def offload_json(manager: SandboxManager, conversation_id: str, prefix: str, payload: Any) -> str:
    """Ghi payload đầy đủ vào /workspace/input/<prefix>_<n>.json. → đường dẫn tương đối /workspace."""
    name = f"{prefix}_{next(_seq)}.json"
    data = json.dumps(payload, ensure_ascii=False, indent=1, default=str).encode("utf-8")
    manager.get(conversation_id).put_file(f"{INPUT_DIR}/{name}", data)
    return f"input/{name}"


def fit_or_offload(manager: SandboxManager, conversation_id: str, prefix: str, payload: dict[str, Any],
                   summarize: Callable[[dict[str, Any]], dict[str, Any]], max_tokens: int,
                   count_tokens: Callable[[str], int] = approx_tokens) -> tuple[dict[str, Any], str | None]:
    """payload ≤ max_tokens → trả nguyên; lớn hơn → ghi file + trả summarize(payload) kèm `full_result_file`."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if count_tokens(text) <= max_tokens:
        return payload, None
    path = offload_json(manager, conversation_id, prefix, payload)
    short = summarize(payload)
    short["full_result_file"] = path
    short["note"] = (f"Khối có truncated=true bị CẮT bớt nội dung; bản đầy đủ ở /workspace/{path}. Đọc bằng read_file "
                     f"(đọc tiếp bằng offset) hoặc json.load trong run_code để lấy đủ số liệu, KHÔNG kết luận thiếu "
                     f"khi chưa đọc file.")
    return short, path


def is_error_content(content: Any) -> bool:
    """JSON {"ok": false} (trừ khi người dùng từ chối – không tính là lỗi tool)."""
    if not isinstance(content, str) or not content.startswith("{"):
        return False
    try:
        obj = json.loads(content)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("ok") is False and not obj.get("rejected")


class ToolGuardMiddleware(AgentMiddleware):
    """Bọc mọi tool call: exception → lỗi có cấu trúc; đánh dấu status lỗi; kết quả quá dài → file sandbox."""

    def __init__(self, manager: SandboxManager, max_tokens: int, count_tokens: Callable[[str], int] = approx_tokens):
        super().__init__()
        self.manager = manager
        self.max_tokens = max_tokens
        self.count_tokens = count_tokens

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        try:
            result = handler(request)
        except GraphBubbleUp:                    # interrupt (xác nhận tool write) phải lan ra ngoài
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("tool %s lỗi: %s", call["name"], e)
            return ToolMessage(fail(f"{type(e).__name__}: {e}"), tool_call_id=call["id"], name=call["name"],
                               status="error")
        if isinstance(result, ToolMessage):
            return self._fix(result, request)
        if isinstance(result, Command) and isinstance(result.update, dict):
            msgs = [self._fix(m, request) if isinstance(m, ToolMessage) else m
                    for m in result.update.get("messages") or []]
            return Command(update={**result.update, "messages": msgs}, goto=result.goto, graph=result.graph)
        return result

    def _fix(self, msg: ToolMessage, request) -> ToolMessage:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False,
                                                                               default=str)
        if is_error_content(content) or (content.startswith("Error") and msg.status != "success"):
            msg.status = "error"
        name = request.tool_call["name"]
        if name not in SELF_MANAGED and self.count_tokens(content) > self.max_tokens:
            try:
                cid = runtime_context(request.runtime).conversation_id
                path = offload_json(self.manager, cid, f"tool_{name}", {"tool": name, "content": content})
                msg.content = json.dumps({"ok": msg.status != "error", "truncated": True,
                                          "preview": clip(content, self.max_tokens),
                                          "full_result_file": path,
                                          "note": f"Kết quả quá dài – bản đầy đủ ở /workspace/{path}"},
                                         ensure_ascii=False)
            except Exception as e:  # noqa: BLE001
                log.warning("offload kết quả %s lỗi: %s", name, e)
                msg.content = clip(content, self.max_tokens * 3)
        return msg
