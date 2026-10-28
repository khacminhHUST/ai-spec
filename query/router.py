"""N1. Router – Fast LLM → JSON (RouterOutput). Lỗi parse: retry `router.json_retry` lần rồi fallback."""
from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from query.deps import Deps, message_text, parse_json_object
from query.models import DISCIPLINES, STREAMS, RouterOutput

log = logging.getLogger(__name__)

SYSTEM = f"""Bạn là bộ định tuyến câu hỏi cho trợ lý tra cứu tài liệu dự án cơ điện (MEP).
Nhiệm vụ: viết lại câu hỏi cuối của người dùng cho đủ nghĩa (dựa vào lịch sử), rồi phân tích. CHỈ trả về 1 JSON object, không giải thích.

Các trường:
- rewritten_question: câu hỏi đầy đủ, độc lập ngữ cảnh, cùng ngôn ngữ với người dùng.
- project_action: "set" (người dùng chuyển sang dự án/công trình khác), "add" (thêm dự án để so sánh), "remove" (bỏ bớt dự án), "keep" (không nhắc dự án mới).
- project_mentions: tên/mã dự án hoặc công trình ĐƯỢC NHẮC TRONG CÂU HỎI CUỐI, chép đúng chữ người dùng viết (VD "S5", "Bãi Đất Đỏ"). Không nhắc → []. Không tự bịa mã.
- disciplines: phân hệ liên quan, chọn trong {DISCIPLINES}. Không chắc → [].
- intent_type: TECHNICAL_SPEC_LOOKUP | COMPARE | CONTRACT_TERM | STANDARD_LOOKUP | OTHER.
- streams: luồng tài liệu cần tra, chọn trong {STREAMS}. LEGAL_STANDARD = TCVN/QCVN; OPERATOR_STANDARD = tiêu chuẩn đơn vị vận hành (Marriott/Westin); DESIGN_CONSULTANT = thuyết minh, spec, chỉ dẫn kỹ thuật của tư vấn; CONTRACT = hợp đồng, phụ lục, giá trị/điều khoản hợp đồng. Không chắc → [] (tra tất cả).
- queries_vi: 1-3 truy vấn tìm kiếm tiếng Việt (từ khóa kỹ thuật, không dấu hỏi).
- queries_en: 1-3 truy vấn tiếng Anh tương đương (tài liệu có bản tiếng Anh).
- exact_keywords: mã/giá trị kỹ thuật cần khớp chính xác (VD "1.5mm2", "LSZH", "IEC 60331", "ASHRAE 62.1"). Không có → [].
"""


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
    human = (f"Dự án người dùng có quyền:\n{proj}\n\nDự án đang chọn: {act}\n\n"
             f"Lịch sử gần nhất:\n{_history_text(messages, turns)}\n\nCâu hỏi cuối: {question}\n\nJSON:")
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
