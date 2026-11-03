"""Server MCP khai trong config/mcp.yaml: nạp tool 1 lần lúc khởi động (langchain-mcp-adapters), gắn nhãn access
read | write vào tool.metadata. Mỗi server là 1 nguồn trong danh mục nguồn của agent (mô tả: khóa `description`).

Access: tools.<tên>.access > access của server > "write" (không khai báo coi là write).
Tool MCP chỉ có bản async → bọc thêm bản sync (mỗi lần gọi mở session mới, theo cơ chế mặc định của adapter).
Server lỗi khi nạp → bỏ qua, ghi log (không làm sập app).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from langchain_core.tools import BaseTool, StructuredTool

from app.services.agent.mcp.session import run_sync

log = logging.getLogger(__name__)

_CONN_KEYS = {"access", "tools", "description"}
_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _subst(v: Any) -> Any:
    """${VAR} → biến môi trường (secret trong .env)."""
    if isinstance(v, str):
        return _VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, list):
        return [_subst(x) for x in v]
    if isinstance(v, dict):
        return {k: _subst(x) for k, x in v.items()}
    return v


def server_connections(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: _subst({k: v for k, v in (s or {}).items() if k not in _CONN_KEYS})
            for name, s in (cfg.get("servers") or {}).items()}


def tool_access(cfg: dict[str, Any], server: str, tool_name: str) -> str:
    s = (cfg.get("servers") or {}).get(server) or {}
    t = (s.get("tools") or {}).get(tool_name) or {}
    access = str(t.get("access") or s.get("access") or "write").lower()
    return access if access in ("read", "write") else "write"


def _with_sync(t: BaseTool, server: str, original: str, access: str) -> BaseTool:
    coro = t.coroutine  # type: ignore[attr-defined]
    return StructuredTool(
        name=t.name, description=t.description, args_schema=t.args_schema, coroutine=coro,
        func=lambda **kw: run_sync(lambda: coro(**kw)), response_format=t.response_format,
        metadata={**(t.metadata or {}), "access": access, "mcp_server": server, "mcp_tool": original},
        handle_tool_error=True,
    )


async def aload_mcp_tools(cfg: dict[str, Any], client_factory: Callable[..., Any] | None = None) -> list[BaseTool]:
    if client_factory is None:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client_factory = MultiServerMCPClient
    conns = server_connections(cfg)
    if not conns:
        return []
    prefix = bool(cfg.get("tool_name_prefix", True))
    client = client_factory(conns, tool_name_prefix=prefix)
    out: list[BaseTool] = []
    for server in conns:
        try:
            tools = await client.get_tools(server_name=server)
        except Exception as e:  # noqa: BLE001
            log.error("Nạp MCP server %s lỗi: %s", server, e)
            continue
        for t in tools:
            original = t.name[len(server) + 1:] if prefix and t.name.startswith(server + "_") else t.name
            out.append(_with_sync(t, server, original, tool_access(cfg, server, original)))
    return out


def load_mcp_tools(cfg: dict[str, Any], client_factory: Callable[..., Any] | None = None) -> list[BaseTool]:
    return run_sync(lambda: aload_mcp_tools(cfg, client_factory))
