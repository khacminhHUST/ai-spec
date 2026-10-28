"""Reranker đa ngữ (config/pipeline.yaml → rerank). Score ∈ [0, 1]."""
from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache

import httpx

from app.core.config import Settings, get_settings
from app.providers.embedding import retry
from app.utils.sparse import tokenize


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


class LocalQwenReranker(Reranker):
    """Qwen3-Reranker (0.6B/4B/8B) chạy cục bộ qua transformers; score = P("yes") ∈ [0, 1].

    Prompt theo model card Qwen3-Reranker. GPU nếu có (fp16), không thì CPU.
    """

    _PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
               "Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
    _SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def __init__(self, cfg: dict):
        self.model = cfg["model"]
        self.instruction = cfg.get("instruction") or "Given a web search query, retrieve relevant passages that answer the query"
        self.batch_size = int(cfg.get("batch_size", 16))
        self.max_length = int(cfg.get("max_length", 1024))
        self._tok, self._lm, self._device = _load_local_reranker(self.model, cfg.get("device", "auto"))
        self._prefix_ids = self._tok.encode(self._PREFIX, add_special_tokens=False)
        self._suffix_ids = self._tok.encode(self._SUFFIX, add_special_tokens=False)
        self._yes = self._tok.convert_tokens_to_ids("yes")
        self._no = self._tok.convert_tokens_to_ids("no")

    def score(self, query: str, documents: list[str]) -> list[float]:
        import torch

        out: list[float] = []
        budget = self.max_length - len(self._prefix_ids) - len(self._suffix_ids)
        order = sorted(range(len(documents)), key=lambda j: len(documents[j]))  # gom độ dài gần nhau → ít padding
        docs = [documents[j] for j in order]
        for i in range(0, len(docs), self.batch_size):
            pairs = [f"<Instruct>: {self.instruction}\n<Query>: {query}\n<Document>: {d}"
                     for d in docs[i : i + self.batch_size]]
            enc = self._tok(pairs, padding=False, truncation="longest_first",
                            return_attention_mask=False, max_length=budget)
            enc["input_ids"] = [self._prefix_ids + ids + self._suffix_ids for ids in enc["input_ids"]]
            batch = self._tok.pad(enc, padding=True, return_tensors="pt").to(self._device)
            with torch.no_grad():
                logits = self._lm(**batch, logits_to_keep=1).logits[:, -1, :]  # chỉ tính logits token cuối
            pair = torch.stack([logits[:, self._no], logits[:, self._yes]], dim=1).float()
            out += torch.nn.functional.softmax(pair, dim=1)[:, 1].tolist()
        result = [0.0] * len(documents)
        for j, s in zip(order, out):
            result[j] = s
        return result


@lru_cache(maxsize=2)
def _load_local_reranker(model: str, device: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(model, padding_side="left")
    lm = AutoModelForCausalLM.from_pretrained(
        model, dtype=torch.float16 if device == "cuda" else torch.float32
    ).to(device).eval()
    return tok, lm, device


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
    if cfg["provider"] == "local":
        return LocalQwenReranker(cfg)
    if cfg["provider"] == "deepinfra":
        return DeepInfraReranker(cfg, s.api_key("deepinfra"))
    raise ValueError(f"provider reranker không hỗ trợ: {cfg['provider']}")
