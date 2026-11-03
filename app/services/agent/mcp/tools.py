"""Tool của connector theo user: tools/list có cache, phân loại đọc/ghi, bọc thành tool LangChain khi agent mở nguồn.

- Cache danh sách tool theo (connector, user) trong `tool_cache_ttl_s` (connector none/headers: dùng chung user "").
- Phân loại (ưu tiên trên xuống): ghi đè trên giao diện > annotation MCP (readOnlyHint=true → read;
  destructiveHint=true / không có annotation → write) > mặc định write. Ghi đè `disabled` → không đưa cho agent.
- Mỗi lần gọi tool mở phiên MCP mới (adapter); OAuth qua ConnectorAuth của user đang chat.
- Thử kết nối thành công mà connector chưa có mô tả → tự điền mô tả gợi ý (mcp/describe.py).
- ToolError / isError từ server, hoặc kết nối hết hạn → lỗi có cấu trúc (ok:false), không làm sập vòng lặp.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import Tool

from app.services.agent.mcp.auth import ConnectorAuth
from app.services.agent.mcp.oauth.errors import ReauthRequired
from app.services.agent.mcp.oauth.token_store import CONNECTED, FORBIDDEN, NEEDS_REAUTH, TokenStore
from app.services.agent.mcp.registry import ConnectorRegistry
from app.services.agent.mcp.session import (
    ServerListing, alisting, connection, find_exc, http_status, run_sync, short_error,
)
from app.services.agent.tools.base import fail

log = logging.getLogger(__name__)

ACCESS_VALUES = ("read", "write", "disabled")
REAUTH_MSG = "Kết nối {name} đã hết hạn – người dùng cần bấm Kết nối lại (tab Kết nối)"
FORBIDDEN_MSG = "Tài khoản chưa được cấp quyền trên server {name} – liên hệ quản trị Server A"


def annotation_access(tool: Tool) -> str:
    a = tool.annotations
    if a is not None and a.readOnlyHint is True:
        return "read"
    return "write"          # destructiveHint=true, không có annotation, hoặc không rõ → write (an toàn)


def effective_access(tool: Tool, override: str | None) -> str:
    return override if override in ACCESS_VALUES else annotation_access(tool)


class ConnectorTools:
    def __init__(self, registry: ConnectorRegistry, tokens: TokenStore, *, ttl_s: float, timeout_s: float,
                 clock: Callable[[], float] = time.monotonic,
                 describe: Callable[[dict[str, Any], ServerListing], str] | None = None):
        self.registry = registry
        self.tokens = tokens
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s
        self.clock = clock
        self.describe = describe
        self._cache: dict[tuple[str, str], tuple[float, list[Tool]]] = {}
        self._mu = threading.Lock()

    # ---------------------------------------------------------------- kết nối / cache
    def connection_for(self, row: dict[str, Any], user_id: str) -> dict[str, Any]:
        auth = ConnectorAuth(self.tokens, user_id, row) if row["auth_type"] == "oauth" else None
        headers = self.registry.headers_for(row) if row["auth_type"] == "headers" else None
        return connection(row["url"], row["transport"], timeout_s=self.timeout_s, headers=headers, auth=auth)

    @staticmethod
    def _key(row: dict[str, Any], user_id: str) -> tuple[str, str]:
        return row["connector_id"], user_id if row["auth_type"] == "oauth" else ""

    def invalidate(self, connector_id: str, user_id: str | None = None) -> None:
        with self._mu:
            for k in [k for k in self._cache if k[0] == connector_id and (user_id is None or k[1] in (user_id, ""))]:
                del self._cache[k]

    def listing(self, row: dict[str, Any], user_id: str) -> ServerListing:
        """initialize + tools/list (bỏ cache, ghi lại cache tool)."""
        conn = self.connection_for(row, user_id)
        out = run_sync(lambda: alisting(conn))
        with self._mu:
            self._cache[self._key(row, user_id)] = (self.clock() + self.ttl_s, out.tools)
        return out

    def mcp_tools(self, row: dict[str, Any], user_id: str, *, fresh: bool = False) -> list[Tool]:
        hit = self._cache.get(self._key(row, user_id))
        if hit and not fresh and self.clock() < hit[0]:
            return hit[1]
        return self.listing(row, user_id).tools

    # ---------------------------------------------------------------- lỗi → trạng thái / thông điệp
    def explain(self, row: dict[str, Any], exc: BaseException) -> tuple[str, str]:
        """→ (status, thông điệp cho người dùng/agent). status: needs_reauth | forbidden | error."""
        name = row.get("display_name") or row["name"]
        status = http_status(exc)
        if find_exc(exc, ReauthRequired) is not None or (status == 401 and row["auth_type"] == "oauth"):
            return NEEDS_REAUTH, REAUTH_MSG.format(name=name)
        if status == 403 and row["auth_type"] == "oauth":
            return FORBIDDEN, FORBIDDEN_MSG.format(name=name)
        if status is not None:
            return "error", f"Connector {name} trả HTTP {status}"
        return "error", f"Không gọi được connector {name}: {short_error(exc)}"

    def probe(self, row: dict[str, Any], user_id: str) -> dict[str, Any]:
        """initialize + tools/list (bỏ cache). OAuth: cập nhật status của user (connected / forbidden / needs_reauth)."""
        try:
            listing = self.listing(row, user_id)
        except Exception as e:  # noqa: BLE001
            status, msg = self.explain(row, e)
            if row["auth_type"] == "oauth" and status in (NEEDS_REAUTH, FORBIDDEN):
                self.tokens.set_status(user_id, row["connector_id"], status)
            log.warning("Thử connector %s: %s", row["name"], msg)
            return {"ok": False, "status": status, "message": msg, "tool_count": 0}
        if row["auth_type"] == "oauth":
            self.tokens.set_status(user_id, row["connector_id"], CONNECTED)
        if not (row.get("description") or "").strip() and self.describe is not None:
            self._fill_description(row, listing)
        n = len(listing.tools)
        return {"ok": True, "status": CONNECTED if row["auth_type"] == "oauth" else "ready",
                "message": f"Kết nối được – {n} tool", "tool_count": n}

    def _fill_description(self, row: dict[str, Any], listing: ServerListing) -> None:
        try:
            text = self.describe(row, listing)
            if text:
                self.registry.store.update_connector(row["connector_id"], description=text)
        except Exception as e:  # noqa: BLE001 – thiếu mô tả không chặn kết nối
            log.warning("Tự điền mô tả connector %s lỗi: %s", row["name"], short_error(e))

    # ---------------------------------------------------------------- danh sách tool cho giao diện
    def views(self, row: dict[str, Any], user_id: str) -> list[dict[str, Any]]:
        overrides = self.registry.store.get_overrides(row["connector_id"])
        out = []
        for t in self.mcp_tools(row, user_id):
            ov = overrides.get(t.name)
            out.append({"name": t.name, "full_name": f"{row['name']}_{t.name}", "title": t.title,
                        "description": t.description or "", "default_access": annotation_access(t),
                        "override": ov, "access": effective_access(t, ov),
                        "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else {}})
        return out

    # ---------------------------------------------------------------- tool khi agent mở nguồn
    def connector_tools(self, row: dict[str, Any], user_id: str) -> list[BaseTool]:
        """Tool (đã bọc) của 1 connector cho user; bỏ tool bị tắt. Lỗi kết nối → ném lên cho người gọi."""
        tools = self.mcp_tools(row, user_id)
        overrides = self.registry.store.get_overrides(row["connector_id"])
        conn = self.connection_for(row, user_id)
        out = []
        for t in tools:
            access = effective_access(t, overrides.get(t.name))
            if access != "disabled":
                out.append(self.wrap(row, t, conn, access))
        return out

    def wrap(self, row: dict[str, Any], tool: Tool, conn: dict[str, Any], access: str) -> BaseTool:
        inner = convert_mcp_tool_to_langchain_tool(None, tool, connection=conn, server_name=row["name"],
                                                   tool_name_prefix=True, handle_tool_errors=False)
        call = inner.coroutine

        async def coro(**kwargs: Any):
            try:
                return await call(**kwargs)
            except ToolException as e:                 # ToolError / isError: kết nối vẫn giữ
                return fail(f"Server {row['name']} báo lỗi: {e}"), None
            except Exception as e:  # noqa: BLE001
                status, msg = self.explain(row, e)
                log.warning("Gọi tool %s lỗi: %s", inner.name, msg)
                return fail(msg, needs_reauth=status == NEEDS_REAUTH, forbidden=status == FORBIDDEN), None

        return StructuredTool(
            name=inner.name, description=inner.description, args_schema=inner.args_schema, coroutine=coro,
            func=lambda **kw: run_sync(lambda: coro(**kw)), response_format="content_and_artifact",
            metadata={**(inner.metadata or {}), "access": access, "mcp_server": row["name"], "mcp_tool": tool.name,
                      "connector_id": row["connector_id"], "connector_label": row.get("display_name") or ""},
            handle_tool_error=True,
        )
