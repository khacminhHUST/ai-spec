"""Retrieval 1 nhánh (project × stream × lang) trên Qdrant: dense + BM25 sparse → RRF → group_by doc_id."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any

from qdrant_client import models as qm

from app.core.config import Settings
from app.providers.embedding import Embedder, get_embedder
from app.utils.keys import child_seq
from app.utils.sparse import query_sparse

HIT_FIELDS = ("child_id", "parent_id", "doc_id", "section_key", "lang", "stream", "project_id", "text",
              "raw_text", "page_start", "page_end")


class Retriever(ABC):
    @abstractmethod
    def search(self, *, project_id: str, stream: str, lang: str, queries: list[str], keywords: list[str],
               disciplines: list[str], doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """→ list ChildHit dict (có fusion_score), tối đa top_k_per_doc / tài liệu."""

    @abstractmethod
    def children_of(self, parent_id: str, lang: str) -> list[dict[str, Any]]:
        """Mọi child của 1 parent, sắp theo seq (dùng lấy child lân cận khi parent dài)."""


def discipline_filter(disciplines: list[str]) -> list[str] | None:
    """`disciplines` ∩ (router.disciplines ∪ GENERAL); router rỗng → không lọc."""
    return sorted({*disciplines, "GENERAL"}) if disciplines else None


class QdrantRetriever(Retriever):
    def __init__(self, settings: Settings, client=None, embedders: dict[str, Embedder] | None = None):
        from app.db.client import qdrant

        self.settings = settings
        self.cfg = settings.pipeline["retrieval"]
        self.client = client or qdrant(settings)
        self._embedders = embedders or {}
        self._cache: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def embedder(self, lang: str) -> Embedder:
        with self._lock:
            if lang not in self._embedders:
                self._embedders[lang] = get_embedder(lang, self.settings)
            return self._embedders[lang]

    def _embed(self, lang: str, text: str) -> list[float]:
        key = (lang, text)
        if key not in self._cache:
            vec = self.embedder(lang).embed_query(text)
            with self._lock:
                if len(self._cache) > 2000:
                    self._cache.clear()
                self._cache[key] = vec
        return self._cache[key]

    def _filter(self, project_id: str, stream: str, disciplines: list[str], doc_ids: list[str] | None) -> qm.Filter:
        must: list[Any] = [
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
            qm.FieldCondition(key="stream", match=qm.MatchValue(value=stream)),
            qm.FieldCondition(key="is_latest", match=qm.MatchValue(value=True)),
        ]
        disc = discipline_filter(disciplines)
        if disc:
            must.append(qm.FieldCondition(key="disciplines", match=qm.MatchAny(any=disc)))
        if doc_ids:
            must.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=list(doc_ids))))
        return qm.Filter(must=must)

    def search(self, *, project_id, stream, lang, queries, keywords, disciplines, doc_ids=None):
        queries = [q for q in queries if q.strip()]
        if not queries:
            return []
        k = int(self.cfg["top_k_per_doc"])
        flt = self._filter(project_id, stream, disciplines, doc_ids)
        prefetch = [qm.Prefetch(query=self._embed(lang, q), using="dense", filter=flt, limit=k * 5) for q in queries]
        sp = query_sparse(" ".join(queries), keywords, float(self.cfg.get("keyword_boost", 1.0)))
        if sp["indices"]:
            prefetch.append(qm.Prefetch(query=qm.SparseVector(**sp), using="sparse", filter=flt, limit=k * 5))
        res = self.client.query_points_groups(
            self.settings.collection(lang), prefetch=prefetch, query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            query_filter=flt, group_by="doc_id", group_size=k, limit=20, with_payload=True,
        )
        hits = []
        for g in res.groups:
            for p in g.hits:
                hit = {f: p.payload.get(f) for f in HIT_FIELDS}
                hit["fusion_score"] = float(p.score)
                hits.append(hit)
        return hits

    def children_of(self, parent_id: str, lang: str) -> list[dict[str, Any]]:
        points, _ = self.client.scroll(
            self.settings.collection(lang),
            scroll_filter=qm.Filter(must=[qm.FieldCondition(key="parent_id", match=qm.MatchValue(value=parent_id))]),
            limit=1000, with_payload=True, with_vectors=False,
        )
        kids = [{f: p.payload.get(f) for f in HIT_FIELDS} for p in points]
        return sorted(kids, key=lambda c: child_seq(c["child_id"]))
