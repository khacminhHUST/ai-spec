"""Dựng vòng lặp agent: LLM ⇄ tools với giới hạn bước/thời gian/lỗi, quản lý ngữ cảnh, xác nhận tool write.

Phiên bản chốt (2026-10-29): langchain 1.4.7 `create_agent` + middleware; deepagents 0.7.32 (PatchToolCallsMiddleware).
Không dùng `deepagents.create_deep_agent`: bản 0.7.32 bắt buộc FilesystemMiddleware (tool read_file/write_file/ls/
execute… trùng tên, khác ngữ nghĩa với tool sandbox của spec) và tự thêm subagent `task` (ngoài phạm vi) – chỉ tắt
được qua HarnessProfile đăng ký toàn cục theo model. Spec cho phép dùng create_agent khi API Deep Agents không phù hợp.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.config import Settings, get_settings
from app.prompts import render
from app.providers.llm import MockChatModel
from app.services.agent.loop.confirm import ConfirmMiddleware
from app.services.agent.loop.context import ToolGuardMiddleware, approx_tokens
from app.services.agent.loop.limits import LimitsMiddleware
from app.services.agent.sandbox import OUTPUT_DIR, SandboxManager
from app.services.agent.skills import Skill, skills_prompt
from app.services.agent.state import AgentContext, HarnessState, history_to_messages, strip_files_note
from app.services.agent.tools.base import clip
from app.services.agent.tools.registry import ToolSet
from app.services.query.generator import cited_numbers

SSE_PREVIEW_CHARS = 200
RECURSION_PER_STEP = 8      # số node LangGraph tối đa cho 1 bước (model + middleware + tools)
NO_ANSWER = "Tôi chưa tạo được câu trả lời. Vui lòng thử lại hoặc nói rõ hơn yêu cầu."


def get_agent_model(cfg: dict[str, Any], settings: Settings | None = None) -> BaseChatModel:
    """cfg = agent.yaml › agent. Provider thiếu key / bị mock → MockChatModel."""
    s = settings or get_settings()
    if s.is_mock(cfg["provider"]):
        return _MockAgentModel()
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {}
    if cfg["provider"] == "google_genai" and cfg.get("thinking_level"):
        kwargs["thinking_config"] = {"thinking_level": cfg["thinking_level"]}
    return init_chat_model(cfg["model"], model_provider=cfg["provider"], api_key=s.api_key(cfg["provider"]),
                           temperature=float(cfg.get("temperature", 0.1)), **kwargs)


def system_prompt(skills: dict[str, Skill]) -> str:
    return render("agent/system", skills=skills_prompt(skills))


def build_agent(model: BaseChatModel, toolset: ToolSet, skills: dict[str, Skill], cfg: dict[str, Any],
                manager: SandboxManager, checkpointer: Any = None, *,
                count_tokens: Callable[[str], int] = approx_tokens, clock: Callable[[], float] = time.monotonic):
    """cfg = toàn bộ agent.yaml. Middleware ngoài cùng trước: giới hạn → tóm tắt → vá tool call → guard → confirm."""
    a = cfg["agent"]
    middleware = [
        LimitsMiddleware(int(a["max_steps"]), int(a["max_consecutive_tool_errors"]), float(a["max_wall_time_s"]),
                         clock=clock),
        SummarizationMiddleware(model, trigger=("tokens", int(a["summarize_above_tokens"])), keep=("messages", 20)),
        PatchToolCallsMiddleware(),
        ToolGuardMiddleware(manager, int(a["tool_result_max_tokens"]), count_tokens),
        ConfirmMiddleware(toolset.write_tools),
    ]
    return create_agent(model, toolset.tools, system_prompt=system_prompt(skills), middleware=middleware,
                        state_schema=HarnessState, context_schema=AgentContext, checkpointer=checkpointer,
                        name="agent")


class _MockAgentModel(MockChatModel):
    """Mock không gọi tool (provider bị mock / thiếu key)."""

    def bind_tools(self, tools, **kwargs):
        return self


# ------------------------------------------------------------------ vào / ra

def agent_input(ctx: AgentContext, question: str, attachments: list[str] | None = None) -> dict[str, Any]:
    text = question
    if attachments:
        text += "\n\n[File đính kèm: " + ", ".join(f"/workspace/input/{a}" for a in attachments) + "]"
    return {"messages": [*history_to_messages(ctx.history), HumanMessage(text)],
            "active_projects": list(ctx.active_projects)}


def tool_summary(content: Any) -> tuple[bool, str]:
    """ToolMessage.content → (ok, tóm tắt ngắn cho SSE tool_end)."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    try:
        obj = json.loads(text)
    except ValueError:
        return True, clip(text, SSE_PREVIEW_CHARS)
    if not isinstance(obj, dict):
        return True, clip(text, SSE_PREVIEW_CHARS)
    success = obj.get("ok") is not False
    if not success:
        return False, clip(str(obj.get("error") or "lỗi"), SSE_PREVIEW_CHARS)
    if "status" in obj:
        return True, f"{obj['status']}: {len(obj.get('blocks') or [])} mục"
    if obj.get("new_files"):
        return True, "file: " + ", ".join(f["path"] for f in obj["new_files"])
    return True, "xong"


def stream_agent(agent: Any, ctx: AgentContext, question: str, attachments: list[str] | None, max_steps: int,
                 emit: Callable[[str, Any], None]) -> dict[str, Any]:
    """Chạy vòng lặp trong node của assistant graph, phát tool_start / tool_end. → state cuối.

    Khi resume sau xác nhận, LangGraph bỏ qua input và chạy tiếp từ checkpoint của namespace con.
    Interrupt (tool write) lan ra assistant graph dưới dạng GraphInterrupt.
    """
    final: dict[str, Any] = {}
    config = {"recursion_limit": max_steps * RECURSION_PER_STEP + 20}
    for mode, chunk in agent.stream(agent_input(ctx, question, attachments), config, context=ctx,
                                    stream_mode=["updates", "values"]):
        if mode == "values":
            final = chunk
            continue
        for update in chunk.values():
            for item in update if isinstance(update, list) else [update]:
                for m in (item or {}).get("messages") or [] if isinstance(item, dict) else []:
                    if isinstance(m, AIMessage):
                        for c in m.tool_calls:
                            preview = json.dumps(c.get("args") or {}, ensure_ascii=False, default=str)
                            emit("tool_start", {"tool": c["name"], "args_preview": clip(preview, SSE_PREVIEW_CHARS)})
                    elif isinstance(m, ToolMessage):
                        success, summary = tool_summary(m.content)
                        emit("tool_end", {"tool": m.name, "ok": success, "summary": summary})
    return final


def final_answer(messages: list) -> str:
    """Text cuối của agent (bỏ ghi chú file nội bộ nếu LLM chép lại từ lịch sử – file đã có sự kiện riêng)."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return strip_files_note(m.text).strip()
    return ""


def answer_citations(answer: str, sources: list[dict]) -> list[dict]:
    """Chỉ nguồn được trích [n] trong câu trả lời (không tự gắn: câu trả lời "không có trong tài liệu" không có nguồn)."""
    by_n = {s["n"]: s for s in sources}
    return [by_n[n] for n in cited_numbers(answer, sources)]


def agent_result(state: dict[str, Any]) -> dict[str, Any]:
    msgs = state.get("messages") or []
    answer = final_answer(msgs) or NO_ANSWER
    return {"answer": answer, "citations": answer_citations(answer, state.get("sources") or []),
            "active_projects": list(state.get("active_projects") or []), "stop_reason": state.get("stop_reason")}


# ------------------------------------------------------------------ file kết quả

def output_snapshot(manager: SandboxManager, conversation_id: str) -> dict[str, tuple[int, float]]:
    sbx = manager.peek(conversation_id)
    return {f.path: (f.size, f.mtime) for f in sbx.list(OUTPUT_DIR)} if sbx else {}


def changed_files(before: dict[str, tuple[int, float]], after: dict[str, tuple[int, float]]) -> list[dict]:
    """File mới/đổi trong /workspace/output → [{name, size}] (name tương đối output/)."""
    prefix = OUTPUT_DIR + "/"
    return [{"name": p[len(prefix):], "size": s} for p, (s, m) in sorted(after.items())
            if before.get(p) != (s, m) and p.startswith(prefix)]
