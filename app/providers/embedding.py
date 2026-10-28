"""Embedding theo ngôn ngữ (config/models.yaml). Ingest và query bắt buộc dùng cùng model / ngôn ngữ.

get_embedder("vi") → DeepInfra Qwen3-Embedding-8B · get_embedder("en") → Gemini embedding-2.
Provider bị mock (MEP_MOCK hoặc thiếu key) → MockEmbedder cùng số chiều.
"""
from __future__ import annotations

import hashlib
import math
import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

import httpx

from app.core.config import Settings, get_settings
from app.utils.sparse import tokenize

T = TypeVar("T")


def retry(fn: Callable[[], T], attempts: int = 4, base_delay: float = 1.0) -> T:
    for i in range(attempts):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == attempts - 1:
                raise
            time.sleep(base_delay * 2**i)
    raise AssertionError("unreachable")


class Embedder(ABC):
    lang: str
    model: str
    dim: int
    batch_size: int = 32

    @abstractmethod
    def _embed(self, texts: list[str], is_query: bool) -> list[list[float]]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            out += retry(lambda: self._embed(batch, is_query=False))
        return out

    def embed_query(self, text: str) -> list[float]:
        return retry(lambda: self._embed([text], is_query=True))[0]


class DeepInfraEmbedder(Embedder):
    BASE_URL = "https://api.deepinfra.com/v1/openai"

    def __init__(self, cfg: dict, api_key: str, lang: str = "vi"):
        self.lang, self.model, self.dim = lang, cfg["model"], int(cfg["dimensions"])
        self.query_instruction = cfg.get("query_instruction", "")
        self._client = httpx.Client(
            base_url=self.BASE_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=60
        )

    def _embed(self, texts: list[str], is_query: bool) -> list[list[float]]:
        if is_query:
            texts = [self.query_instruction + t for t in texts]
        r = self._client.post(
            "/embeddings",
            json={"model": self.model, "input": texts, "encoding_format": "float", "dimensions": self.dim},
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


class GeminiEmbedder(Embedder):
    batch_size = 100

    def __init__(self, cfg: dict, api_key: str, lang: str = "en"):
        from google import genai

        self.lang, self.model, self.dim = lang, cfg["model"], int(cfg["dimensions"])
        self.task_doc = cfg.get("task_type_document", "RETRIEVAL_DOCUMENT")
        self.task_query = cfg.get("task_type_query", "RETRIEVAL_QUERY")
        self._client = genai.Client(api_key=api_key)

    def _embed(self, texts: list[str], is_query: bool) -> list[list[float]]:
        from google.genai import types

        # mỗi text 1 Content riêng – list[str] bị gemini-embedding-2 gộp thành 1 vector
        res = self._client.models.embed_content(
            model=self.model,
            contents=[types.Content(parts=[types.Part(text=t)]) for t in texts],
            config=types.EmbedContentConfig(
                task_type=self.task_query if is_query else self.task_doc, output_dimensionality=self.dim
            ),
        )
        vecs = [e.values for e in res.embeddings]
        if len(vecs) != len(texts):
            raise RuntimeError(f"Gemini trả {len(vecs)} vector cho {len(texts)} text")
        return vecs


class MockEmbedder(Embedder):
    """Không gọi mạng: hashed bag-of-tokens → vector chuẩn hóa (text trùng từ → cosine cao)."""

    batch_size = 1000

    def __init__(self, cfg: dict, lang: str):
        self.lang, self.model, self.dim = lang, f"mock:{cfg['model']}", int(cfg["dimensions"])

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in tokenize(text):
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            v[idx] += 1.0 if h[4] & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def _embed(self, texts: list[str], is_query: bool) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def get_embedder(lang: str, settings: Settings | None = None) -> Embedder:
    s = settings or get_settings()
    cfg = s.embedding_cfg(lang)
    provider = cfg["provider"]
    if s.is_mock(provider):
        return MockEmbedder(cfg, lang)
    key = s.api_key(provider)
    if provider == "deepinfra":
        return DeepInfraEmbedder(cfg, key, lang)
    if provider == "google":
        return GeminiEmbedder(cfg, key, lang)
    raise ValueError(f"provider embedding không hỗ trợ: {provider}")
