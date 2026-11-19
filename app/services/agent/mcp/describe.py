"""Mô tả ngắn của 1 nguồn (connector) cho danh mục nguồn của agent – người thêm connector xem lại và sửa được.

Gợi ý theo thứ tự: phần giới thiệu server tự trả khi kết nối (instructions / mô tả server – tùy chọn trong giao thức)
nếu đủ ngắn → LLM tóm tắt từ giới thiệu + danh sách tool → không có LLM: giới thiệu cắt ở cuối câu / tên tool.
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


def _clip(text: str, limit: int) -> str:
    """Gộp 1 dòng, dài quá thì cắt ở cuối câu gần nhất (không để câu lấp lửng); không có câu nào vừa → cắt ở từ."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(p) for p in (". ", "! ", "? ", "; "))
    if end >= limit // 3:
        return head[:end + 1]
    return head[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-—") + "…"


def tools_text(listing: ServerListing) -> str:
    return "\n".join(f"- {t.name}: {_one_line(t.description or '', TOOL_DESC_CHARS)}"
                     for t in listing.tools[:TOOLS_MAX]) or "(không có tool)"


def suggest_description(row: dict[str, Any], listing: ServerListing, llm: BaseChatModel | None = None) -> str:
    """Mô tả ≤ DESC_MAX_CHARS. Giới thiệu của server vừa giới hạn → dùng nguyên; dài hơn hoặc không có →
    LLM tóm tắt (từ giới thiệu + danh sách tool); không có LLM → cắt giới thiệu ở cuối câu / liệt kê tên tool."""
    own = " ".join((listing.instructions or listing.about or "").split())
    if own and len(own) <= DESC_MAX_CHARS:
        return own
    if llm is not None and (own or listing.tools):
        try:
            prompt = render("agent/source_describe", source_name=row.get("display_name") or row["name"],
                            about=own or "(không có)", tools=tools_text(listing))
            msg = llm.invoke([HumanMessage(prompt)])
            text = _clip(str(getattr(msg, "text", None) or msg.content), DESC_MAX_CHARS)
            if text:
                return text
        except Exception as e:  # noqa: BLE001 – LLM lỗi: dùng giới thiệu cắt ở cuối câu / danh sách tên tool
            log.warning("LLM tóm tắt mô tả connector lỗi: %s", e)
    if own:
        return _clip(own, DESC_MAX_CHARS)
    names = ", ".join(t.name for t in listing.tools[:TOOLS_MAX])
    return _one_line(f"Công cụ: {names}", DESC_MAX_CHARS) if names else ""
