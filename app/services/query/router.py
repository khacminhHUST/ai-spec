"""N1. Router – Fast LLM → JSON (RouterOutput). Lỗi parse: retry `router.json_retry` lần rồi fallback."""
from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.models.query import DISCIPLINES, STREAMS, RouterOutput
from app.prompts import render
from app.services.query.deps import Deps, message_text, parse_json_object

log = logging.getLogger(__name__)

SYSTEM = render("query/router_system", disciplines=DISCIPLINES, streams=STREAMS)


def _history_text(messages: list[dict], turns: int) -> str:
    recent = messages[-2 * turns :] if turns > 0 else []
    lines = []
    for m in recent:
        content = str(m.get("content", ""))
        if m.get("role") == "assistant" and len(content) > 600:
            content = content[:600] + "…"
        lines.append(f"{'Người dùng' if m.get('role') == 'user' else 'Trợ lý'}: {content}")
    return "\n".join(lines) or "(chưa có)"


def build_messages(question: str, messages: list[dict], active: list[str], user_projects: list[dict],
                   turns: int) -> list:
    proj = "\n".join(f"- {p['name']} (gọi tắt: {', '.join(p.get('aliases', []))})" for p in user_projects) or "(không có)"
    act = ", ".join(active) or "(chưa chọn)"
    human = render("query/router_human", projects=proj, active=act, history=_history_text(messages, turns),
                   question=question)
    return [SystemMessage(SYSTEM), HumanMessage(human)]


def run_router(deps: Deps, question: str, messages: list[dict], active_names: list[str],
               user_projects: list[dict]) -> tuple[RouterOutput, bool]:
    """→ (RouterOutput, fallback_used)."""
    cfg = deps.cfg("router")
    msgs = build_messages(question, messages, active_names, user_projects, int(cfg.get("history_turns", 6)))
    attempts = 1 + int(cfg.get("json_retry", 1))
    for i in range(attempts):
        try:
            raw = message_text(deps.llm_fast.invoke(msgs))
            out = RouterOutput.model_validate(parse_json_object(raw))
            if not out.rewritten_question.strip():
                out.rewritten_question = question
            if not out.queries_vi and not out.queries_en:
                out.queries_vi = [out.rewritten_question]
            return out, False
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            log.warning("router: JSON lỗi lần %d: %s", i + 1, e)
        except Exception as e:  # noqa: BLE001 – lỗi gọi LLM cũng fallback
            log.warning("router: lỗi gọi LLM lần %d: %s", i + 1, e)
    return RouterOutput.fallback(question), True
