"""Giới hạn vòng lặp agent – lưới an toàn ép bằng code (bình thường hầu như không chạm).

Trần cứng: max_steps (số lần gọi LLM, gồm bước trả lời), max_consecutive_tool_errors, max_wall_time_s (thời gian chạy
thực – lúc chờ người dùng xác nhận tool write không tính), và MAX_BLOCKED_STEPS bước liên tiếp mà mọi tool call đều bị
chặn vì hết lượt tra. Chạm trần → lần gọi LLM kế tiếp là **bước trả lời ép buộc**: không tool (kể cả tool nguồn ngoài
đã mở – SourcesMiddleware tự bỏ khi state.force_answer), thêm lời nhắc cuối, timeout `forced_answer_timeout_s`, không
thử lại. LLM vẫn sinh tool call → bỏ; không còn chữ / gọi lỗi → thông báo dừng cố định. stop_reason được ghi (PARTIAL).

Lượt tra: max_search_rounds = số bước LLM có gọi search_project_docs mà có kết quả (gọi song song trong 1 bước tính 1
bước; bước mà mọi lần tra đều NOT_FOUND không tính – còn được tra lại), max_search_rounds_total = trần tổng số bước tra.
Đủ số bước → đóng tra cứu: các bước sau bỏ tool search khỏi danh sách; lời nhắc "trả lời bằng dữ liệu đã có" chỉ gắn ở bước
ngay sau khi đóng và bước ngay sau khi có call bị chặn (không gắn mọi bước: tránh ép trả lời giữa luồng xuất file, giữ
cache tiền tố). Gemini vẫn có thể gọi tool đã bỏ → trả ngay BUDGET_EXHAUSTED, không chạy. 0 = không giới hạn.

Gọi LLM lỗi (429/timeout sau khi SDK đã thử lại) ở bước thường mà lượt đã có kết quả công cụ → thử 1 bước trả lời ép
buộc; vẫn lỗi → thông báo dừng cố định (không làm mất lượt bằng lỗi SSE).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from app.services.agent.state import AgentContext, HarnessState

log = logging.getLogger(__name__)

SEARCH_TOOL = "search_project_docs"
BUDGET_STATUS = "BUDGET_EXHAUSTED"
MAX_BLOCKED_STEPS = 2          # số bước liên tiếp mọi tool call đều bị chặn → ép trả lời
LLM_ERROR = "lỗi gọi mô hình ngôn ngữ"

FORCE_NOTE = ("[Hệ thống] Đã chạm giới hạn: {reason}. KHÔNG gọi công cụ nào nữa. Trả lời người dùng NGAY bằng kết quả "
              "đã có ở trên (giữ trích dẫn [n]); nói rõ là đã dừng sớm và phần nào chưa làm xong / chưa tra được, gợi ý "
              "người dùng hỏi tiếp phần đó.")
SEARCH_NOTE = ("[Hệ thống] Đã hết lượt tra tài liệu cho câu hỏi này ({reason}): KHÔNG gọi search_project_docs nữa. "
               "Trả lời bằng dữ liệu đã có ở trên (giữ trích dẫn [n]). Ý đã tra mà không tìm thấy → nói chưa tìm "
               "thấy trong tài liệu và gợi ý hỏi cụ thể hơn (VD nêu số bảng/mục); ý CHƯA tra → nói chưa tra được do giới hạn lượt tra và gợi ý người dùng hỏi tiếp riêng ý đó "
               "(không nói là tài liệu không có). Người dùng yêu cầu tạo file → tiếp tục tạo file bằng dữ liệu đã có.")
BLOCKED_MSG = ("Không chạy: {reason}. Trả lời bằng dữ liệu đã có; ý chưa tra được → nói chưa tra được do giới hạn lượt "
               "tra, gợi ý người dùng hỏi tiếp ý đó (không nói là tài liệu không có).")


def consecutive_tool_errors(messages: list) -> int:
    """Số ToolMessage lỗi liên tiếp tính từ cuối (bỏ qua AIMessage xen giữa; gặp tool thành công → dừng)."""
    n = 0
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            if m.status != "error":
                break
            n += 1
        elif not isinstance(m, AIMessage):
            break
    return n


def is_blocked_msg(m: Any) -> bool:
    return isinstance(m, ToolMessage) and isinstance(m.content, str) and f'"status": "{BUDGET_STATUS}"' in m.content


def _search_status(m: Any) -> str | None:
    if not isinstance(m, ToolMessage) or not isinstance(m.content, str):
        return None
    try:
        return (json.loads(m.content) or {}).get("status")
    except (ValueError, AttributeError):
        return None


def search_rounds(messages: list) -> tuple[int, int]:
    """→ (số bước tra có kết quả, tổng số bước tra). Bước tra = AIMessage có gọi search_project_docs (song song trong 1
    bước tính 1). Bước mà mọi lần tra đều NOT_FOUND / lỗi không tính là "có kết quả": tìm kiếm đôi khi trượt (router
    viết lại câu khác mỗi lần) nên còn được tra lại – trần tổng max_search_rounds_total chặn lặp vô hạn."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    useful = total = 0
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        calls = [c for c in m.tool_calls if c.get("name") == SEARCH_TOOL]
        if not calls or any(c.get("id") not in results for c in calls):
            continue
        total += 1
        if any(_search_status(results[c["id"]]) in ("OK", "PARTIAL") for c in calls):
            useful += 1
    return useful, total


def consecutive_blocked_steps(messages: list) -> int:
    """Số bước LLM liên tiếp (tính từ cuối) mà mọi tool call đều bị chặn vì hết lượt tra."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    n = 0
    for m in reversed(messages):
        if not isinstance(m, AIMessage):
            continue
        if m.tool_calls and all(is_blocked_msg(results.get(c.get("id"))) for c in m.tool_calls):
            n += 1
        else:
            break
    return n


def last_step_blocked(messages: list) -> bool:
    """Bước LLM vừa rồi có tool call bị chặn."""
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return False
        if is_blocked_msg(m):
            return True
    return False


def has_tool_results(messages: list) -> bool:
    """Lượt đã có kết quả công cụ dùng được (lịch sử lượt trước chỉ có text nên mọi ToolMessage thuộc lượt này)."""
    return any(isinstance(m, ToolMessage) and m.status != "error" and not is_blocked_msg(m) for m in messages)


def last_ai_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls and m.text.strip():
            return m.text.strip()
    return ""


def stop_text(reason: str, done: str = "") -> str:
    return f"Tôi phải dừng vì {reason}." + (f"\n\nPhần đã làm:\n{done}" if done else "") + \
        "\n\nCác file đã tạo (nếu có) được gửi kèm. Bạn có thể yêu cầu làm tiếp hoặc chia nhỏ yêu cầu."


class LimitsMiddleware(AgentMiddleware):
    """Đặt NGOÀI CÙNG: before/after_model đếm + quyết định; wrap_model_call bỏ tool search / lời nhắc / bước ép;
    wrap_tool_call chặn search khi đã đóng tra cứu."""

    state_schema = HarnessState

    def __init__(self, max_steps: int, max_consecutive_tool_errors: int, max_wall_time_s: float,
                 clock: Callable[[], float] = time.monotonic, *, max_search_rounds: int = 0,
                 max_search_rounds_total: int = 0, forced_answer_timeout_s: float = 0):
        super().__init__()
        self.max_steps = max_steps
        self.max_errors = max_consecutive_tool_errors
        self.max_wall = max_wall_time_s
        self.clock = clock
        self.max_rounds = max_search_rounds
        self.max_rounds_total = max_search_rounds_total or max_search_rounds
        self.forced_timeout = forced_answer_timeout_s

    @classmethod
    def from_cfg(cls, a: dict[str, Any], clock: Callable[[], float] = time.monotonic) -> "LimitsMiddleware":
        """a = agent.yaml › agent."""
        return cls(int(a["max_steps"]), int(a["max_consecutive_tool_errors"]), float(a["max_wall_time_s"]), clock,
                   max_search_rounds=int(a.get("max_search_rounds", 0)),
                   max_search_rounds_total=int(a.get("max_search_rounds_total", 0)),
                   forced_answer_timeout_s=float(a.get("forced_answer_timeout_s", 0)))

    # ---------------------------------------------------------------- thời gian chạy thực

    def _active(self, state: HarnessState, runtime: Any) -> tuple[float, float]:
        """→ (giây chạy thực của lượt, now). Lần chạy đầu sau resume (AgentContext mới, chưa đánh dấu) → khoảng từ lần
        đo trước tới giờ là thời gian chờ xác nhận → không cộng."""
        now = self.clock()
        tick = state.get("tick")
        active = float(state.get("active_s") or 0.0)
        ctx = getattr(runtime, "context", None)
        resumed = isinstance(ctx, AgentContext) and not ctx.loop_running
        if isinstance(ctx, AgentContext):
            ctx.loop_running = True
        if tick is not None and not resumed:
            active += max(0.0, now - float(tick))
        return active, now

    def before_agent(self, state: HarnessState, runtime: Any) -> dict[str, Any]:
        now = self.clock()
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, AgentContext):
            ctx.loop_running = True
        return {"step_count": 0, "started_at": now, "tick": now, "active_s": 0.0, "stop_reason": None,
                "force_answer": None, "search_rounds": 0, "search_closed": None, "search_closed_at": None}

    # ---------------------------------------------------------------- quyết định trước mỗi lần gọi LLM

    def _hard_reason(self, state: HarnessState, active: float) -> str | None:
        msgs = state.get("messages") or []
        if self.max_steps and int(state.get("step_count") or 0) >= self.max_steps - 1:   # lần gọi cuối = trả lời
            return f"đã đạt giới hạn {self.max_steps} bước xử lý"
        if self.max_errors and consecutive_tool_errors(msgs) >= self.max_errors:
            return f"công cụ lỗi {self.max_errors} lần liên tiếp"
        if self.max_wall and active > self.max_wall:
            return f"vượt thời gian tối đa {int(self.max_wall)} giây"
        if consecutive_blocked_steps(msgs) >= MAX_BLOCKED_STEPS:
            return "đã hết lượt tra mà vẫn cố tra tiếp"
        return None

    def before_model(self, state: HarnessState, runtime: Any) -> dict[str, Any]:
        active, now = self._active(state, runtime)
        update: dict[str, Any] = {"active_s": active, "tick": now}
        reason = self._hard_reason(state, active)
        if reason:
            return {**update, "force_answer": reason, "stop_reason": reason}
        if self.max_rounds and not state.get("search_closed"):
            useful, total = search_rounds(state.get("messages") or [])
            if useful >= self.max_rounds or total >= self.max_rounds_total:
                update.update(search_closed=f"đã tra {total} bước",
                              search_closed_at=int(state.get("step_count") or 0))
        return update

    def wrap_model_call(self, request, handler):
        st = request.state or {}
        if st.get("force_answer"):
            return self._forced(request, handler, str(st["force_answer"]))
        closed = st.get("search_closed")
        if closed:
            tools = [t for t in request.tools if getattr(t, "name", None) != SEARCH_TOOL]
            just_closed = st.get("search_closed_at") == int(st.get("step_count") or 0)
            if just_closed or last_step_blocked(request.messages):
                request = request.override(tools=tools, messages=[*request.messages,
                                                                  HumanMessage(SEARCH_NOTE.format(reason=closed))])
            else:
                request = request.override(tools=tools)
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as e:  # noqa: BLE001 – không để mất kết quả đã tra vì 1 lần gọi LLM lỗi
            if not has_tool_results(request.messages):
                raise
            log.warning("gọi LLM agent lỗi (%s) – thử bước trả lời ép buộc", e)
            resp = self._forced(request, handler, LLM_ERROR)
            return ExtendedModelResponse(model_response=_as_response(resp),
                                         command=Command(update={"stop_reason": LLM_ERROR}))

    def _forced(self, request, handler, reason: str):
        """Bước trả lời ép buộc: không tool, lời nhắc cuối, timeout ngắn, 1 lần thử; lỗi → thông báo dừng cố định."""
        settings = dict(request.model_settings or {})
        if self.forced_timeout:
            settings.update(timeout=self.forced_timeout, max_retries=1)
        req = request.override(tools=[], tool_choice=None, model_settings=settings,
                               messages=[*request.messages, HumanMessage(FORCE_NOTE.format(reason=reason))])
        try:
            resp = handler(req)
        except GraphBubbleUp:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("bước trả lời ép buộc lỗi: %s", e)
            return AIMessage(stop_text(reason, last_ai_text(request.messages)))
        return _strip_tool_calls(resp, reason, request.messages)

    def after_model(self, state: HarnessState, runtime: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"step_count": int(state.get("step_count") or 0) + 1}
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        if isinstance(last, AIMessage) and any(c.get("name") == SEARCH_TOOL for c in last.tool_calls):
            out["search_rounds"] = int(state.get("search_rounds") or 0) + 1
        return out

    # ---------------------------------------------------------------- chặn search khi đã đóng tra cứu

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        closed = (request.state or {}).get("search_closed")
        if call.get("name") != SEARCH_TOOL or not closed:
            return handler(request)
        return ToolMessage(json.dumps({"ok": True, "status": BUDGET_STATUS, "message": BLOCKED_MSG.format(reason=closed)},
                                      ensure_ascii=False), tool_call_id=call["id"], name=call["name"])


def _as_response(resp: Any) -> ModelResponse:
    if isinstance(resp, AIMessage):
        return ModelResponse(result=[resp])
    return getattr(resp, "model_response", resp)


def _strip_tool_calls(response: Any, reason: str, messages: list) -> Any:
    """Bước trả lời ép buộc: bỏ mọi tool call LLM vẫn sinh ra; không còn chữ → thông báo dừng cố định."""
    def fix(m: Any) -> Any:
        if isinstance(m, AIMessage) and (m.tool_calls or not m.text.strip()):
            text = m.text.strip() or stop_text(reason, last_ai_text(messages))
            return AIMessage(text, id=m.id, response_metadata=m.response_metadata, usage_metadata=m.usage_metadata)
        return m

    if isinstance(response, AIMessage):
        return fix(response)
    inner = getattr(response, "model_response", response)          # ExtendedModelResponse → ModelResponse
    if getattr(inner, "result", None):
        inner.result = [fix(m) for m in inner.result]
    return response
