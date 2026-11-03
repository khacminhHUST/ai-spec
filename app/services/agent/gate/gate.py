"""G0. Gate – Fast LLM → JSON {route, reason}. Lỗi parse → retry → fallback lookup.

Cờ force_route và file đính kèm quyết định bằng code (không gọi LLM): có đính kèm luôn là agent.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.prompts import load, render
from app.services.agent.gate.schema import GateOutput
from app.services.query.deps import message_text, parse_json_object

log = logging.getLogger(__name__)

HISTORY_CLIP = 400
EXTERNAL_TOOLS_MAX = 12


def history_text(messages: list[dict], turns: int) -> str:
    """Lượt gần nhất, ghi rõ lượt trước có tạo file / chạy agent để nhận biết tiếp nối tác vụ."""
    lines = []
    for m in messages[-2 * turns:] if turns > 0 else []:
        content = str(m.get("content") or "")[:HISTORY_CLIP]
        if m.get("role") == "user":
            lines.append(f"Người dùng: {content}")
        else:
            tag = "[tác vụ agent] " if m.get("route") == "agent" else ""
            files = ", ".join(f["name"] for f in m.get("files") or [])
            lines.append(f"Trợ lý: {tag}{content}" + (f" [file: {files}]" if files else ""))
    return "\n".join(lines) or "(chưa có)"


def external_text(systems: list[dict[str, Any]] | None) -> str:
    """Hệ thống ngoài (MCP) user đang dùng được → vài dòng cho Gate: tên + các tool (mô tả rút gọn)."""
    lines = []
    for s in systems or []:
        tools = "; ".join(f"{t['name']}: {t['description']}" if t.get("description") else t["name"]
                          for t in s.get("tools", [])[:EXTERNAL_TOOLS_MAX])
        lines.append(f"- {s['name']} ({s['label']}): {tools}" if s.get("label") else f"- {s['name']}: {tools}")
    return "\n".join(lines) or "(không có)"


def run_gate(llm: BaseChatModel, cfg: dict[str, Any], question: str, messages: list[dict],
             attachments: list[str] | None = None, force_route: str | None = None,
             external: list[dict[str, Any]] | None = None) -> GateOutput:
    """cfg = agent.yaml › gate. external = hệ thống ngoài user đang dùng được (AgentRuntime.external_systems)."""
    if force_route:
        return GateOutput(route=force_route, reason="force_route")
    if attachments:
        return GateOutput(route="agent", reason="có file đính kèm")
    msgs = [SystemMessage(load("agent/gate_system")),
            HumanMessage(render("agent/gate_human", question=question, has_attachments="không",
                                history=history_text(messages, int(cfg.get("history_turns", 6))),
                                external=external_text(external)))]
    for attempt in range(int(cfg.get("json_retry", 1)) + 1):
        try:
            return GateOutput(**parse_json_object(message_text(llm.invoke(msgs))))
        except (ValueError, TypeError, ValidationError) as e:
            log.warning("Gate JSON lỗi (lần %d): %s", attempt + 1, e)
        except Exception as e:  # noqa: BLE001 – lỗi gọi LLM: không chặn tra cứu
            log.warning("Gate gọi LLM lỗi (lần %d): %s", attempt + 1, e)
    return GateOutput(route="lookup", reason="fallback")
