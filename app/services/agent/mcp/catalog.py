"""Danh mục nguồn ngoài (MCP) của agent: mỗi nguồn 1 dòng – tên, mô tả, trạng thái với user đang chat.

Nguồn = server trong config/mcp.yaml (tool nạp sẵn lúc khởi động) + connector khai qua giao diện (McpHub).
Danh mục chỉ đọc DB/config, không gọi sang server. Tool chi tiết của 1 nguồn chỉ nạp khi agent mở nguồn đó
(`tools()`, dùng cache tools/list của connector).
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

from app.services.agent.mcp.oauth.token_store import CONNECTED, FORBIDDEN, NEEDS_REAUTH

log = logging.getLogger(__name__)

READY, NOT_CONNECTED = "ready", "not_connected"
USABLE = (READY, CONNECTED)
STATUS_TEXT = {
    READY: "sẵn sàng",
    CONNECTED: "đã kết nối",
    NOT_CONNECTED: "chưa kết nối – người dùng cần bấm Kết nối ở tab Kết nối",
    NEEDS_REAUTH: "kết nối hết hạn – người dùng cần bấm Kết nối lại ở tab Kết nối",
    FORBIDDEN: "tài khoản chưa được cấp quyền trên hệ thống này",
}


class SourceUnavailable(Exception):
    """Nguồn không dùng được lúc này (chưa kết nối, hết hạn, không có quyền, server lỗi) – thông điệp cho agent."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def source_entry(name: str, label: str, description: str, status: str) -> dict[str, str]:
    return {"name": name, "label": label or name, "description": " ".join((description or "").split()),
            "status": status}


class SourceCatalog:
    """yaml_tools: tool của server trong config/mcp.yaml (metadata mcp_server). hub: McpHub | None."""

    def __init__(self, yaml_tools: list[BaseTool] | None = None, yaml_cfg: dict[str, Any] | None = None, hub: Any = None):
        self.yaml_tools: dict[str, list[BaseTool]] = {}
        for t in yaml_tools or []:
            self.yaml_tools.setdefault((t.metadata or {}).get("mcp_server") or "", []).append(t)
        self.yaml_cfg = (yaml_cfg or {}).get("servers") or {}
        self.hub = hub

    def entries(self, user_id: str) -> list[dict[str, str]]:
        out = [source_entry(name, name, (self.yaml_cfg.get(name) or {}).get("description", ""), READY)
               for name in self.yaml_tools if name]
        if self.hub is not None:
            try:
                out += [e for e in self.hub.catalog(user_id) if e["name"] not in self.yaml_tools]
            except Exception as e:  # noqa: BLE001 – lỗi đọc connector: agent vẫn chạy với nguồn còn lại
                log.error("Đọc danh mục connector lỗi: %s", type(e).__name__)
        return out

    def tools(self, user_id: str, name: str) -> list[BaseTool]:
        """Tool của 1 nguồn cho user. Không dùng được → SourceUnavailable."""
        if name in self.yaml_tools:
            return list(self.yaml_tools[name])
        if self.hub is None:
            raise SourceUnavailable(f"Không có nguồn '{name}'")
        return self.hub.source_tools(user_id, name)
