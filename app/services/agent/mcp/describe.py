"""Mô tả ngắn của 1 nguồn (connector) cho danh mục nguồn của agent – người thêm connector xem lại và sửa được.

Gợi ý theo thứ tự: phần giới thiệu server tự trả khi kết nối (instructions / mô tả server – tùy chọn trong giao thức)
→ LLM tóm tắt từ danh sách tool → danh sách tên tool (không có LLM).
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.prompts import render
from app.services.agent.mcp.session import ServerListing

log = logging.getLogger(__name__)

DESC_MAX_CHARS = 300
TOOL_DESC_CHARS = 120
TOOLS_MAX = 40


def _one_line(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def tools_text(listing: ServerListing) -> str:
    return "\n".join(f"- {t.name}: {_one_line(t.description or '', TOOL_DESC_CHARS)}"
                     for t in listing.tools[:TOOLS_MAX]) or "(không có tool)"


def suggest_description(row: dict[str, Any], listing: ServerListing, llm: BaseChatModel | None = None) -> str:
    own = listing.instructions or listing.about
    if own:
        return _one_line(own, DESC_MAX_CHARS)
    if llm is not None and listing.tools:
        try:
            prompt = render("agent/source_describe", source_name=row.get("display_name") or row["name"],
                            tools=tools_text(listing))
            msg = llm.invoke([HumanMessage(prompt)])
            text = _one_line(str(getattr(msg, "text", None) or msg.content), DESC_MAX_CHARS)
            if text:
                return text
        except Exception as e:  # noqa: BLE001 – LLM lỗi: dùng danh sách tên tool
            log.warning("LLM tóm tắt mô tả connector lỗi: %s", e)
    names = ", ".join(t.name for t in listing.tools[:TOOLS_MAX])
    return _one_line(f"Công cụ: {names}", DESC_MAX_CHARS) if names else ""
