"""Reranker đa ngữ (config/pipeline.yaml → rerank). Score ∈ [0, 1]."""
from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import logging

import httpx

from app.core.config import Settings, get_settings
from app.providers.embedding import retry
from app.utils.sparse import tokenize

log = logging.getLogger(__name__)


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


class GeminiReranker(Reranker):
    """LLM (Gemini flash-lite) chấm 0–10 cả lô đoạn trong 1 lần gọi → score = điểm / 10 ∈ [0, 1].

    Không tốn RAM/CPU server (thay reranker cục bộ khi server không có GPU). Lô lớn → chia `batch_size` đoạn, gọi song
    song. Đoạn LLM bỏ sót / lô gọi lỗi (sau retry) → `missing_score` (mặc định = ngưỡng: vẫn giữ, không loại oan).
    `min_score` (gemini_min_score) ghi đè rerank.min_score khi lọc (app/services/query/retrieval.py).
    """

    def __init__(self, cfg: dict, settings: Settings, llm=None):
        from app.providers.llm import get_chat_model

        self.model = cfg["model"]
        self.batch_size = int(cfg.get("batch_size", 40))
        self.max_chars = int(cfg.get("max_chars", 2000))
        self.min_score = float(cfg.get("gemini_min_score", 0.6))   # thang LLM khác P(yes) → ngưỡng riêng
        self.missing_score = float(cfg.get("missing_score", self.min_score))
        # Lô chậm bất thường (Gemini đôi khi treo 20–30s): sau hedge_after_s gửi thêm 1 bản, lấy bản xong trước;
        # quá max_wait_s → missing_score. Cả hai = 0 → gọi song song như cũ, không hạn thời gian.
        self.hedge_after_s = float(cfg.get("hedge_after_s", 0))
        self.max_wait_s = float(cfg.get("max_wait_s", 0))
        overrides = {"provider": cfg.get("llm_provider", "google_genai"), "model": self.model, "temperature": 0}
        if cfg.get("thinking_level"):
            overrides["thinking_level"] = cfg["thinking_level"]
        if cfg.get("max_wait_s"):
            overrides["timeout"] = float(cfg["max_wait_s"])   # request treo không giữ thread mãi
        self._llm = llm or get_chat_model("fast", settings, **overrides)

    def _messages(self, query: str, docs: list[str]) -> list:
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.prompts import load, render

        passages = "\n\n".join(f"[{i}] {d[: self.max_chars]}" for i, d in enumerate(docs))
        return [SystemMessage(load("query/rerank_system")),
                HumanMessage(render("query/rerank_human", question=query, passages=passages))]

    @staticmethod
    def parse(text: str, n: int) -> dict[int, float]:
        """JSON {"0": 7, "1": 2} (dạng gọn – ít token ra, nhanh ~1s/lô) hoặc {"scores": [{"id", "score"}]} → {id: score/10};
        bỏ id lạ / điểm không phải số."""
        import json
        import re

        m = re.search(r"\{.*\}", text or "", re.S)
        raw = json.loads(m.group(0)) if m else {}
        out: dict[int, float] = {}
        if isinstance(raw, dict) and "scores" not in raw:
            raw = {"scores": [{"id": k, "score": v} for k, v in raw.items()]}
        for it in raw.get("scores") or [] if isinstance(raw, dict) else []:
            try:
                i, sc = int(it["id"]), float(it["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= i < n:
                out[i] = min(max(sc, 0.0), 10.0) / 10
        return out

    def _score_batch(self, query: str, docs: list[str]) -> list[float]:
        from app.providers.llm import message_text

        def call() -> dict[int, float]:
            got = self.parse(message_text(self._llm.invoke(self._messages(query, docs))), len(docs))
            if not got:
                raise RuntimeError("reranker LLM không trả điểm hợp lệ")
            return got

        try:
            got = retry(call, attempts=2, base_delay=0.5)   # câu hỏi đang chờ: thử lại 1 lần, không chờ lâu
        except Exception as e:  # noqa: BLE001 – lô lỗi: giữ nguyên (score trung tính), không làm hỏng câu trả lời
            log.warning("rerank Gemini lỗi (%s) → %d đoạn dùng missing_score", e, len(docs))
            got = {}
        if len(got) < len(docs):
            log.info("rerank Gemini: thiếu %d/%d điểm → missing_score", len(docs) - len(got), len(docs))
        return [got.get(i, self.missing_score) for i in range(len(docs))]

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        batches = [documents[i : i + self.batch_size] for i in range(0, len(documents), self.batch_size)]
        if self.hedge_after_s <= 0 and self.max_wait_s <= 0:
            if len(batches) == 1:
                return self._score_batch(query, batches[0])
            with ThreadPoolExecutor(max_workers=len(batches)) as ex:
                parts = list(ex.map(lambda b: self._score_batch(query, b), batches))
            return [x for part in parts for x in part]
        return [x for part in self._score_hedged(query, batches) for x in part]

    def _score_hedged(self, query: str, batches: list[list[str]]) -> list[list[float]]:
        """Mỗi lô 1 request; lô chưa xong sau hedge_after_s (hoặc lỗi) → thêm 1 request, lấy cái xong trước;
        quá max_wait_s → missing_score. Executor riêng mỗi lần gọi (không tranh worker giữa các câu hỏi); request
        thua chạy nốt trong nền, bị chặn bởi timeout của client LLM."""
        import time
        from concurrent.futures import FIRST_COMPLETED, wait

        start = time.monotonic()
        wait_s = self.max_wait_s or 60.0
        hedge_at = start + (self.hedge_after_s if self.hedge_after_s > 0 else float("inf"))
        deadline = start + wait_s
        ex = ThreadPoolExecutor(max_workers=2 * len(batches), thread_name_prefix="rerank")
        futs = {i: [ex.submit(self._score_batch, query, b)] for i, b in enumerate(batches)}
        out: dict[int, list[float]] = {}
        try:
            while len(out) < len(batches):
                for i, fs in futs.items():
                    if i in out:
                        continue
                    ok = [f for f in fs if f.done() and f.exception() is None]
                    if ok:
                        out[i] = ok[0].result()
                    elif all(f.done() for f in fs):                      # mọi bản đều lỗi
                        if len(fs) < 2:
                            fs.append(ex.submit(self._score_batch, query, batches[i]))
                        else:
                            out[i] = [self.missing_score] * len(batches[i])
                if len(out) == len(batches):
                    break
                now = time.monotonic()
                if now >= deadline:
                    slow = [i for i in futs if i not in out]
                    log.warning("rerank Gemini: %d lô quá %.0fs → missing_score", len(slow), wait_s)
                    for i in slow:
                        out[i] = [self.missing_score] * len(batches[i])
                    break
                if now >= hedge_at:
                    slow = [i for i in futs if i not in out and len(futs[i]) < 2]
                    if slow:
                        log.info("rerank Gemini: %d lô chậm > %.0fs → gửi lại", len(slow), self.hedge_after_s)
                    for i in slow:
                        futs[i].append(ex.submit(self._score_batch, query, batches[i]))
                    hedge_at = float("inf")
                pending = [f for i, fs in futs.items() if i not in out for f in fs if not f.done()]
                if pending:
                    wait(pending, timeout=max(0.0, min(hedge_at, deadline) - time.monotonic()),
                         return_when=FIRST_COMPLETED)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return [out[i] for i in range(len(batches))]


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
    if cfg["provider"] == "gemini":
        return GeminiReranker(cfg, s)
    if cfg["provider"] == "local":
        return LocalQwenReranker(cfg)
    if cfg["provider"] == "deepinfra":
        return DeepInfraReranker(cfg, s.api_key("deepinfra"))
    raise ValueError(f"provider reranker không hỗ trợ: {cfg['provider']}")
