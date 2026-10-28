"""Phụ thuộc của graph (DB, retriever, reranker, LLM) – tiêm vào build_graph để unit test thay bằng mock."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel

from shared.config import Settings, get_settings
from shared.reranker import Reranker, get_reranker

from query.repo import Repo
from query.retriever import Retriever


def _default_count_tokens(text: str) -> int:
    from shared.tokens import count_tokens

    return count_tokens(text)


@dataclass
class Deps:
    settings: Settings
    repo: Repo
    retriever: Retriever
    reranker: Reranker
    llm_fast: BaseChatModel
    llm_main: BaseChatModel
    count_tokens: Callable[[str], int] = _default_count_tokens
    _profile_cache: dict[str, tuple[float, dict]] = field(default_factory=dict)

    # ---- config ----
    def cfg(self, section: str) -> dict[str, Any]:
        return self.settings.pipeline.get(section, {})

    # ---- profile cache (TTL) ----
    def cached_profile(self, project_id: str, loader: Callable[[], dict]) -> dict:
        ttl = float(self.cfg("project").get("profile_cache_ttl_s", 0))
        hit = self._profile_cache.get(project_id)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        prof = loader()
        self._profile_cache[project_id] = (time.monotonic(), prof)
        return prof

    @classmethod
    def default(cls, settings: Settings | None = None, pool=None) -> "Deps":
        from shared.db import pg_pool
        from shared.llm import get_chat_model

        from query.repo import PgRepo
        from query.retriever import QdrantRetriever

        s = settings or get_settings()
        return cls(
            settings=s,
            repo=PgRepo(s, pool or pg_pool(s)),
            retriever=QdrantRetriever(s),
            reranker=get_reranker(s),
            llm_fast=get_chat_model("fast", s),
            llm_main=get_chat_model("main", s),
        )


# ------------------------------------------------------------------ tiện ích LLM

from shared.llm import message_text  # noqa: E402,F401  (re-export cho router/grader/generator)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """JSON object từ output LLM (bỏ ```json fence, lấy {...} ngoài cùng). Lỗi → ValueError."""
    t = _FENCE_RE.sub("", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("không có JSON object")
    obj = json.loads(t[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON không phải object")
    return obj
