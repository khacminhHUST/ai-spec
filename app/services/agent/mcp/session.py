"""Phiên MCP tới connector (langchain-mcp-adapters `create_session`) + đọc lỗi HTTP lồng trong ExceptionGroup.

`alisting`: initialize + tools/list, giữ lại phần giới thiệu server tự trả (instructions, mô tả server – đều tùy chọn).

Transport của SDK `mcp` chạy request trong task group → lỗi 401/403 tới tay người gọi dưới dạng ExceptionGroup
chứa httpx.HTTPStatusError; `find_exc` / `http_status` dò cả nhóm lẫn __cause__/__context__.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

import httpx
from langchain_mcp_adapters.sessions import create_session
from mcp.types import Tool

MAX_PAGES = 50


def connection(url: str, transport: str, *, timeout_s: float, headers: dict[str, str] | None = None,
               auth: httpx.Auth | None = None) -> dict[str, Any]:
    """Connection (TypedDict) của adapter cho streamable_http | sse."""
    conn: dict[str, Any] = {"transport": transport, "url": url}
    if headers:
        conn["headers"] = dict(headers)
    if auth is not None:
        conn["auth"] = auth
    if transport == "sse":
        conn["timeout"] = timeout_s
    else:
        conn["timeout"] = timedelta(seconds=timeout_s)
    return conn


@dataclass
class ServerListing:
    tools: list[Tool] = field(default_factory=list)
    instructions: str = ""          # InitializeResult.instructions
    about: str = ""                 # serverInfo.title / description (bản giao thức mới)


async def alisting(conn: dict[str, Any]) -> ServerListing:
    """initialize + tools/list (đủ các trang)."""
    out = ServerListing()
    async with create_session(conn) as session:
        init = await session.initialize()
        info = getattr(init, "serverInfo", None)
        out.instructions = (getattr(init, "instructions", None) or "").strip()
        out.about = " – ".join(x for x in (getattr(info, "title", None), getattr(info, "description", None)) if x)
        cursor = None
        for _ in range(MAX_PAGES):
            page = await session.list_tools(cursor=cursor)
            out.tools.extend(page.tools or [])
            cursor = page.nextCursor
            if not cursor:
                break
    return out


def _walk(exc: BaseException, seen: set[int] | None = None):
    seen = seen if seen is not None else set()
    if exc is None or id(exc) in seen:
        return
    seen.add(id(exc))
    yield exc
    for sub in getattr(exc, "exceptions", None) or []:
        yield from _walk(sub, seen)
    for nxt in (exc.__cause__, exc.__context__):
        if nxt is not None:
            yield from _walk(nxt, seen)


def find_exc(exc: BaseException, cls: type | tuple[type, ...]) -> BaseException | None:
    return next((e for e in _walk(exc) if isinstance(e, cls)), None)


def http_status(exc: BaseException) -> int | None:
    e = find_exc(exc, httpx.HTTPStatusError)
    return e.response.status_code if e is not None else None


def short_error(exc: BaseException) -> str:
    """Lỗi gốc ngắn gọn (bỏ lớp ExceptionGroup) – không chứa header/token."""
    leaves = [e for e in _walk(exc) if not getattr(e, "exceptions", None)]
    e = leaves[0] if leaves else exc
    return f"{type(e).__name__}: {e}"[:300]


def run_sync(coro_factory: Callable[[], Any]) -> Any:
    """Chạy coroutine từ code sync (kể cả khi thread hiện tại đã có event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    box: dict[str, Any] = {}

    def target():
        try:
            box["v"] = asyncio.run(coro_factory())
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]
