"""Nạp nguồn ngoài theo yêu cầu (2 tầng).

- Mỗi lần gọi LLM: thêm danh mục nguồn (mỗi nguồn 1 dòng, lấy từ AgentContext.sources) vào cuối system prompt, và
  thêm tool của các nguồn đã mở (state `open_sources`) vào bộ tool gửi cho LLM.
- Tool call tới tool của nguồn đã mở (không đăng ký sẵn trong agent) → gắn tool tương ứng để chạy. Nguồn đã đóng /
  mất kết nối → để ConfirmMiddleware trả lỗi có cấu trúc.
Tool của nguồn lấy qua SourceCatalog (cache tools/list theo connector + user).
"""
from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool

from app.services.agent.mcp.catalog import STATUS_TEXT, SourceCatalog, SourceUnavailable
from app.services.agent.state import HarnessState, runtime_context
from app.services.agent.tools.source_tool import NAME as OPEN_TOOL

log = logging.getLogger(__name__)


def catalog_text(sources: list[dict[str, str]], opened: list[str]) -> str:
    if not sources:
        return "Nguồn dữ liệu ngoài: (chưa có nguồn nào được kết nối)"
    lines = [f"Nguồn dữ liệu ngoài – mở bằng {OPEN_TOOL}(name) trước khi dùng; nguồn [đã mở] thì công cụ đã có sẵn:"]
    for s in sources:
        label = f" ({s['label']})" if s.get("label") and s["label"] != s["name"] else ""
        desc = f": {s['description']}" if s.get("description") else ""
        state = "đã mở" if s["name"] in opened else STATUS_TEXT.get(s["status"], s["status"])
        lines.append(f"- {s['name']}{label}{desc} [{state}]")
    return "\n".join(lines)


class SourcesMiddleware(AgentMiddleware):
    state_schema = HarnessState

    def __init__(self, catalog: SourceCatalog):
        super().__init__()
        self.catalog = catalog

    def _open_tools(self, state: Any, runtime: Any) -> dict[str, BaseTool]:
        ctx = runtime_context(runtime)
        out: dict[str, BaseTool] = {}
        for name in (state or {}).get("open_sources") or []:
            try:
                tools = self.catalog.tools(ctx.user_id, name)
            except SourceUnavailable as e:
                log.info("Nguồn %s không dùng được: %s", name, e.message)
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("Nạp tool nguồn %s lỗi: %s", name, e)
                continue
            for t in tools:
                out.setdefault(t.name, t)
        return out

    def wrap_model_call(self, request, handler):
        ctx = runtime_context(request.runtime)
        have = {t.name for t in request.tools if isinstance(t, BaseTool)}
        extra = [t for n, t in self._open_tools(request.state, request.runtime).items() if n not in have]
        text = catalog_text(ctx.sources, list((request.state or {}).get("open_sources") or []))
        system = SystemMessage(f"{request.system_prompt or ''}\n\n{text}".strip())
        return handler(request.override(tools=[*request.tools, *extra], system_message=system))

    def wrap_tool_call(self, request, handler):
        if request.tool is None:
            t = self._open_tools(request.state, request.runtime).get(request.tool_call["name"])
            if t is not None:
                request = request.override(tool=t)
        return handler(request)
