"""Dựng vòng lặp agent: LLM ⇄ tools với giới hạn bước/thời gian/lỗi, quản lý ngữ cảnh, xác nhận tool write.

Phiên bản chốt (2026-10-29): langchain 1.4.7 `create_agent` + middleware; deepagents 0.7.32 (PatchToolCallsMiddleware).
Không dùng `deepagents.create_deep_agent`: bản 0.7.32 bắt buộc FilesystemMiddleware (tool read_file/write_file/ls/
execute… trùng tên, khác ngữ nghĩa với tool sandbox của spec) và tự thêm subagent `task` (ngoài phạm vi) – chỉ tắt
được qua HarnessProfile đăng ký toàn cục theo model. Spec cho phép dùng create_agent khi API Deep Agents không phù hợp.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import Settings, get_settings
from app.prompts import render
from app.services.agent.loop.confirm import ConfirmMiddleware
from app.services.agent.loop.context import ToolGuardMiddleware, approx_tokens
from app.services.agent.loop.limits import LimitsMiddleware
from app.services.agent.sandbox import OUTPUT_DIR, SandboxManager
from app.services.agent.skills import Skill, skills_prompt
from app.services.agent.state import AgentContext, HarnessState, history_to_messages
from app.services.agent.tools.registry import ToolSet
from app.services.query.generator import cited_numbers

FALLBACK_CITATIONS = 5


def get_agent_model(cfg: dict[str, Any], settings: Settings | None = None) -> BaseChatModel:
    """cfg = agent.yaml › agent. Provider thiếu key / bị mock → MockChatModel."""
    s = settings or get_settings()
    if s.is_mock(cfg["provider"]):
        from app.providers.llm import MockChatModel

        return MockChatModel()
    from langchain.chat_models import init_chat_model

    return init_chat_model(cfg["model"], model_provider=cfg["provider"], api_key=s.api_key(cfg["provider"]),
                           temperature=float(cfg.get("temperature", 0.1)))


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


# ------------------------------------------------------------------ vào / ra

def agent_input(ctx: AgentContext, question: str, attachments: list[str] | None = None) -> dict[str, Any]:
    text = question
    if attachments:
        text += "\n\n[File đính kèm: " + ", ".join(f"/workspace/input/{a}" for a in attachments) + "]"
    return {"messages": [*history_to_messages(ctx.history), HumanMessage(text)],
            "active_projects": list(ctx.active_projects)}


def final_answer(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return m.text.strip()
    return ""


def answer_citations(answer: str, sources: list[dict]) -> list[dict]:
    """Nguồn được trích [n] trong câu trả lời; không trích → các nguồn đã dùng (khử trùng, tối đa 5)."""
    if not sources:
        return []
    by_n = {s["n"]: s for s in sources}
    ns = cited_numbers(answer, sources)
    if ns:
        return [by_n[n] for n in ns]
    seen, out = set(), []
    for s in sources:
        key = (s["doc_id"], s["section_key"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out[:FALLBACK_CITATIONS]


def agent_result(state: dict[str, Any]) -> dict[str, Any]:
    msgs = state.get("messages") or []
    answer = final_answer(msgs)
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
