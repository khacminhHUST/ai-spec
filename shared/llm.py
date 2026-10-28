"""LLM qua interface chung LangChain `BaseChatModel` (không khóa provider).

get_chat_model("fast" | "main")  → config/pipeline.yaml › llm
get_chat_model("chunking")       → config/models.yaml › chunking_llm
Provider bị mock → MockChatModel (trả lời theo `responses` hoặc `handler`).
"""
from __future__ import annotations

from typing import Any, Callable, Iterator, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from shared.config import Settings, get_settings

Role = Literal["fast", "main", "chunking"]


def llm_cfg(role: Role, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    return s.models["chunking_llm"] if role == "chunking" else s.pipeline["llm"][role]


class MockChatModel(BaseChatModel):
    """Mock không gọi mạng.

    - `handler(messages) -> str` nếu có; ngược lại lần lượt trả `responses` (hết thì lặp phần tử cuối).
    - `calls` ghi lại messages của từng lần gọi (để test đếm số lần gọi LLM).
    """

    responses: list[str] = ["MOCK"]
    handler: Callable[[list[BaseMessage]], str] | None = None
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "mock-chat"

    def _next(self, messages: list[BaseMessage]) -> str:
        self.calls.append(messages)
        if self.handler:
            return self.handler(messages)
        i = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[i]

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._next(messages)))])

    def _stream(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        text = self._next(messages)
        for i in range(0, len(text), 8):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text[i : i + 8]))
            if run_manager:
                run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk


def get_chat_model(role: Role, settings: Settings | None = None, **overrides: Any) -> BaseChatModel:
    s = settings or get_settings()
    cfg = {**llm_cfg(role, s), **overrides}
    if s.is_mock(cfg["provider"]):
        return MockChatModel()
    from langchain.chat_models import init_chat_model

    return init_chat_model(
        cfg["model"], model_provider=cfg["provider"], temperature=cfg.get("temperature", 0),
        api_key=s.api_key(cfg["provider"]),
    )
