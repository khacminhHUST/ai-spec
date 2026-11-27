"""Unit – hedged request cho chunk đầu (loop/hedge.py): chọn request ra chunk đầu trước, không trộn/không trùng chunk,
lỗi, và qua vòng lặp agent thật (stream_agent): sự kiện token / tool_start chỉ của request thắng, phát đúng 1 lần."""
import threading
import time

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_google_genai import ChatGoogleGenerativeAI

import app.services.agent.loop.hedge as H
from app.services.agent.loop.agent import agent_result, stream_agent
from app.services.agent.loop.hedge import HedgedChatGoogleGenerativeAI, hedged_stream

pytestmark = pytest.mark.unit

FAST, SLOW, T = 0.0, 1.0, 0.2       # độ trễ chunk đầu (s), hạn chờ chunk đầu


def source(plan):
    """plan[i] = (trễ chunk đầu, [item…] hoặc Exception, lỗi giữa chừng?) cho lần start() thứ i."""
    calls, lock = [], threading.Lock()

    def start():
        with lock:
            i = len(calls)
            calls.append(i)
        delay, items, *rest = plan[i]

        def gen():
            time.sleep(delay)
            if isinstance(items, Exception):
                raise items
            for x in items:
                yield x
            if rest and rest[0]:
                raise rest[0]
        return gen()
    return start, calls


@pytest.fixture
def hedges(monkeypatch):
    seen = []
    monkeypatch.setattr(H, "note_hedge", lambda attempt, waited: seen.append(attempt))
    return seen


def test_fast_first_chunk_no_hedge(hedges):
    start, calls = source([(FAST, ["a", "b", "c"])])
    assert list(hedged_stream(start, T)) == ["a", "b", "c"]
    assert calls == [0] and hedges == []


def test_slow_first_chunk_uses_second_request_only(hedges):
    start, calls = source([(SLOW, ["A1", "A2"]), (FAST, ["B1", "B2", "B3"])])
    t = time.monotonic()
    assert list(hedged_stream(start, T)) == ["B1", "B2", "B3"]
    assert time.monotonic() - t < SLOW
    assert calls == [0, 1] and hedges == [1]


def test_first_request_still_wins_when_hedge_slower(hedges):
    start, calls = source([(0.4, ["A1", "A2"]), (SLOW * 2, ["B1"])])
    assert list(hedged_stream(start, T)) == ["A1", "A2"]
    assert calls == [0, 1]


def test_only_one_hedge(hedges):
    start, calls = source([(SLOW, ["A"]), (SLOW, ["B"]), (FAST, ["C"])])
    assert list(hedged_stream(start, T)) in (["A"], ["B"])
    assert calls == [0, 1] and hedges == [1]


def test_error_before_deadline_raises_without_hedge(hedges):
    start, calls = source([(FAST, RuntimeError("429"))])
    with pytest.raises(RuntimeError, match="429"):
        list(hedged_stream(start, T))
    time.sleep(T * 2)
    assert calls == [0] and hedges == []


def test_first_error_after_hedge_waits_for_second(hedges):
    start, _ = source([(0.4, RuntimeError("timeout")), (0.6, ["B1", "B2"])])
    assert list(hedged_stream(start, T)) == ["B1", "B2"]


def test_both_error_raises_first(hedges):
    start, _ = source([(0.4, RuntimeError("đầu")), (0.5, ValueError("sau"))])
    with pytest.raises(RuntimeError, match="đầu"):
        list(hedged_stream(start, T))


def test_winner_error_mid_stream_propagates(hedges):
    start, _ = source([(FAST, ["a"], RuntimeError("đứt"))])
    out = []
    with pytest.raises(RuntimeError, match="đứt"):
        for x in hedged_stream(start, T):
            out.append(x)
    assert out == ["a"]


# ------------------------------------------------------------------ qua model LangChain + vòng lặp agent

def _chunk(text="", call=None):
    tc = [{"name": call[0], "args": "{}", "id": call[1], "index": 0}] if call else []
    return ChatGenerationChunk(message=AIMessageChunk(content=text, tool_call_chunks=tc))


def _model(monkeypatch, plan, timeout=T):
    """HedgedChat với _stream của lớp cha giả lập theo plan (không gọi mạng)."""
    start, calls = source(plan)
    monkeypatch.setattr(ChatGoogleGenerativeAI, "_stream", lambda self, *a, **k: start())
    return HedgedChatGoogleGenerativeAI(model="gemini-3.8-flash", api_key="x", first_token_timeout_s=timeout), calls


class Tokens(BaseCallbackHandler):
    def __init__(self):
        self.tokens = []

    def on_llm_new_token(self, token, **kw):
        self.tokens.append(token)


def test_model_invoke_streams_winner_tokens_once(monkeypatch, hedges):
    model, calls = _model(monkeypatch, [(SLOW, [_chunk("CHẬM "), _chunk("x")]), (FAST, [_chunk("Nhanh "), _chunk("[1]")])])
    cb = Tokens()
    out = model.invoke([HumanMessage("hỏi")], config={"callbacks": [cb]}, stream=True)
    assert out.text == "Nhanh [1]"
    assert "".join(cb.tokens) == "Nhanh [1]"
    assert calls == [0, 1]


def test_timeout_zero_disables_hedge(monkeypatch, hedges):
    model, calls = _model(monkeypatch, [(0.3, [_chunk("ok")])], timeout=0)
    assert model.invoke([HumanMessage("hỏi")], stream=True).text == "ok"
    assert calls == [0] and hedges == []


def _build_agent(mock_settings, model):
    """Như tests/agent/unit/loop/test_ag_agent_loop._agent nhưng dùng model HedgedChat (stream giả)."""
    import copy

    from app.services.agent.config import agent_cfg
    from app.services.agent.loop.agent import build_agent
    from app.services.agent.sandbox import SandboxManager
    from app.services.agent.sandbox.mock_backend import MockSandbox
    from app.services.agent.skills import load_skills
    from app.services.agent.tools.registry import build_toolset
    from tests.agent.unit.loop.test_ag_agent_loop import BEND
    from tests.query.fakes import make_deps, scripted_llm

    cfg = copy.deepcopy(agent_cfg())
    skills = load_skills(cfg["skills_dir"])
    manager = SandboxManager(lambda cid: MockSandbox(), 1800)
    ts = build_toolset(make_deps(mock_settings, llm=scripted_llm(BEND)), manager, cfg, skills, [])
    return build_agent(model, ts, skills, cfg, manager)


def test_agent_loop_sse_no_duplicate_tokens_or_tool_calls(monkeypatch, mock_settings, hedges):
    """Bước 1 (gọi tool) và bước 2 (trả lời) đều chậm chunk đầu → request thứ 2 thắng; SSE chỉ có 1 tool_start,
    tool chạy 1 lần, token = đúng câu trả lời của request thắng."""
    from app.services.agent.state import AgentContext
    from tests.agent.fakes import CTX

    plan = [(SLOW, [_chunk(call=("list_files", "c_slow"))]), (FAST, [_chunk(call=("list_files", "c_fast"))]),
            (SLOW, [_chunk("Câu trả lời CHẬM.")]), (FAST, [_chunk("Mục 1 [1]"), _chunk(" xong.")])]
    model, calls = _model(monkeypatch, plan)
    agent = _build_agent(mock_settings, model)
    events = []
    ctx = AgentContext(CTX.user_id, CTX.conversation_id)
    final, streamed = stream_agent(agent, ctx, "Liệt kê file", None, 25, lambda e, d: events.append((e, d)))
    tokens = "".join(d for e, d in events if e == "token")
    assert tokens == streamed == "Mục 1 [1] xong."
    assert [d["tool"] for e, d in events if e == "tool_start"] == ["list_files"]
    assert [d["tool"] for e, d in events if e == "tool_end"] == ["list_files"]
    assert agent_result(final)["answer"] == "Mục 1 [1] xong."
    assert calls == [0, 1, 2, 3] and hedges == [1, 1]
