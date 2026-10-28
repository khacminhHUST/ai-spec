"""Reranker đa ngữ (config/pipeline.yaml → rerank). Score ∈ [0, 1]."""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from shared.config import Settings, get_settings
from shared.embedding import retry
from shared.sparse import tokenize


class Reranker(ABC):
    model: str

    @abstractmethod
    def score(self, query: str, documents: list[str]) -> list[float]: ...


class DeepInfraReranker(Reranker):
    BASE_URL = "https://api.deepinfra.com/v1/inference"

    def __init__(self, cfg: dict, api_key: str):
        self.model = cfg["model"]
        self.instruction = cfg.get("instruction")
        self._client = httpx.Client(
            base_url=self.BASE_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=60
        )

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        body = {"queries": [query], "documents": documents}
        if self.instruction:
            body["instruction"] = self.instruction

        def call() -> list[float]:
            r = self._client.post(f"/{self.model}", json=body)
            r.raise_for_status()
            scores = r.json()["scores"]
            if len(scores) != len(documents):
                raise RuntimeError(f"reranker trả {len(scores)} score cho {len(documents)} document")
            return [float(x) for x in scores]

        return retry(call)


class MockReranker(Reranker):
    """Không gọi mạng: tỷ lệ token của query xuất hiện trong document."""

    model = "mock-reranker"

    def score(self, query: str, documents: list[str]) -> list[float]:
        q = set(tokenize(query))
        if not q:
            return [0.0] * len(documents)
        return [len(q & set(tokenize(d))) / len(q) for d in documents]


def get_reranker(settings: Settings | None = None) -> Reranker:
    s = settings or get_settings()
    cfg = s.pipeline["rerank"]
    if s.is_mock(cfg["provider"]):
        return MockReranker()
    if cfg["provider"] == "deepinfra":
        return DeepInfraReranker(cfg, s.api_key("deepinfra"))
    raise ValueError(f"provider reranker không hỗ trợ: {cfg['provider']}")
