"""open_source_tools: mở 1 nguồn ngoài trong danh mục → tool của nguồn được nạp từ bước sau (loop/sources.py).

Nguồn đã mở ghi vào state `open_sources`, giữ đến hết hội thoại (assistant graph lưu lại giữa các lượt).
"""
from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.services.agent.mcp.catalog import STATUS_TEXT, USABLE, SourceCatalog, SourceUnavailable
from app.services.agent.state import runtime_context
from app.services.agent.tools.base import fail, ok

NAME = "open_source_tools"
DESC = """Mở 1 nguồn dữ liệu ngoài trong mục "Nguồn dữ liệu ngoài" (mail, ERP, Trimble, nhân sự…) để dùng công cụ của nguồn đó.
Dùng khi yêu cầu cần dữ liệu hoặc thao tác ở nguồn đó. Nguồn đã mở thì không mở lại.
- name: tên nguồn đúng như trong danh mục (VD "mail").
Trả về danh sách công cụ của nguồn; các công cụ này gọi được từ bước tiếp theo."""


def make_source_tool(catalog: SourceCatalog) -> BaseTool:
    @tool(NAME, description=DESC)
    def open_source_tools(name: str, runtime: ToolRuntime) -> Command | str:
        ctx = runtime_context(runtime)
        name = (name or "").strip()
        entry = next((s for s in ctx.sources if s["name"] == name), None)
        if entry is None:
            return fail(f"Không có nguồn '{name}'", available=[s["name"] for s in ctx.sources])
        if entry["status"] not in USABLE:
            return fail(f"{entry['label']}: {STATUS_TEXT.get(entry['status'], entry['status'])}",
                        source_status=entry["status"])
        try:
            tools = catalog.tools(ctx.user_id, name)
        except SourceUnavailable as e:
            return fail(e.message)
        text = ok(source=name, tools=[t.name for t in tools],
                  note="Các công cụ trên gọi được từ bước tiếp theo.")
        return Command(update={"messages": [ToolMessage(text, tool_call_id=runtime.tool_call_id, name=NAME)],
                               "open_sources": [name]})

    return open_source_tools
