"""Bước 6 – Embed & lưu: Postgres (documents, parent_chunks) + Qdrant (child_chunks_<lang>, vector dense + sparse).

- Embedding theo ngôn ngữ của child (shared.embedding.get_embedder), batch + retry; cache theo hash(model + text).
- Sparse: shared.sparse.doc_sparse (BM25, cùng tokenizer với Query).
- Idempotent theo file_hash: đã có → bỏ qua (force → xoá rồi ghi lại cùng ID).
- Versioning theo doc_code: bản mới nhất (issue_date, revision, ingested_at) is_latest = true; còn lại false
  ở cả Postgres và payload Qdrant.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from array import array
from datetime import date

from qdrant_client import models as qm

from shared.config import ROOT, Settings, get_settings
from shared.db import pg_connect, qdrant
from shared.embedding import Embedder, get_embedder
from shared.schema import ChildChunk, Document, ParentChunk
from shared.sparse import doc_sparse

log = logging.getLogger(__name__)
EMB_CACHE = ROOT / "artifacts" / "_cache" / "embeddings.sqlite"
_lock = threading.Lock()


# ---------------------------------------------------------------- embedding cache

class EmbeddingCache:
    def __init__(self, path=EMB_CACHE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()

    def get_many(self, keys: list[str]) -> dict[str, list[float]]:
        out = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = "SELECT k, v FROM emb WHERE k IN (%s)" % ",".join("?" * len(chunk))
            for k, v in self._db.execute(q, chunk):
                out[k] = array("f", v).tolist()
        return out

    def put_many(self, items: dict[str, list[float]]) -> None:
        with _lock:
            self._db.executemany("INSERT OR REPLACE INTO emb (k, v) VALUES (?, ?)",
                                 [(k, array("f", v).tobytes()) for k, v in items.items()])
            self._db.commit()


def embed_children(children: list[ChildChunk], embedder: Embedder, cache: EmbeddingCache | None = None) -> list[list[float]]:
    texts = [c.text for c in children]
    use_cache = cache is not None and not embedder.model.startswith("mock:")
    keys = [EmbeddingCache.key(embedder.model, t) for t in texts]
    found = cache.get_many(keys) if use_cache else {}
    todo = [i for i, k in enumerate(keys) if k not in found]
    if todo:
        vecs = embedder.embed_documents([texts[i] for i in todo])
        new = {keys[i]: v for i, v in zip(todo, vecs)}
        if use_cache:
            cache.put_many(new)
        found.update(new)
    return [found[k] for k in keys]


# ---------------------------------------------------------------- Postgres

def find_by_hash(conn, file_hash: str) -> dict | None:
    return conn.execute("SELECT doc_id, doc_code, is_latest FROM documents WHERE file_hash = %s", (file_hash,)).fetchone()


def _insert_document(conn, d: Document) -> None:
    conn.execute(
        """INSERT INTO documents (doc_id, doc_code, project_id, building, stream, disciplines, title, issuer, revision,
               issue_date, stage, languages, is_bilingual, is_latest, source_path, file_hash, ingested_at, warnings)
           VALUES (%(doc_id)s, %(doc_code)s, %(project_id)s, %(building)s, %(stream)s, %(disciplines)s, %(title)s,
               %(issuer)s, %(revision)s, %(issue_date)s, %(stage)s, %(languages)s, %(is_bilingual)s, %(is_latest)s,
               %(source_path)s, %(file_hash)s, %(ingested_at)s, %(warnings)s)""",
        d.model_dump(),
    )


def _insert_parents(conn, parents: list[ParentChunk]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO parent_chunks (parent_id, doc_id, section_key, parent_section_key, lang, type, number, title,
                   breadcrumb, content_md, page_start, page_end, referenced_ids, token_count)
               VALUES (%(parent_id)s, %(doc_id)s, %(section_key)s, %(parent_section_key)s, %(lang)s, %(type)s,
                   %(number)s, %(title)s, %(breadcrumb)s, %(content_md)s, %(page_start)s, %(page_end)s,
                   %(referenced_ids)s, %(token_count)s)""",
            [p.model_dump() for p in parents],
        )


def _version_key(row: dict) -> tuple:
    rev = row.get("revision") or ""
    rev_key = (int(rev), "") if rev.isdigit() else (-1, rev)
    return (row.get("issue_date") or date.min, rev_key, row.get("ingested_at"))


# ---------------------------------------------------------------- Qdrant

def delete_points(client, settings: Settings, doc_id: str) -> None:
    flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    for lang in ("vi", "en"):
        name = settings.collection(lang)
        if client.collection_exists(name):
            client.delete(name, points_selector=qm.FilterSelector(filter=flt), wait=True)


def upsert_children(client, settings: Settings, children: list[ChildChunk], vectors: list[list[float]],
                    batch: int = 128) -> int:
    n = 0
    for lang in ("vi", "en"):
        idx = [i for i, c in enumerate(children) if c.lang == lang]
        name = settings.collection(lang)
        for k in range(0, len(idx), batch):
            pts = []
            for i in idx[k:k + batch]:
                c = children[i]
                sp = doc_sparse(c.text)
                pts.append(qm.PointStruct(
                    id=c.point_id,
                    vector={"dense": vectors[i], "sparse": qm.SparseVector(indices=sp["indices"], values=sp["values"])},
                    payload=c.qdrant_payload(),
                ))
            if pts:
                client.upsert(name, points=pts, wait=True)
                n += len(pts)
    return n


def set_latest_payload(client, settings: Settings, doc_id: str, is_latest: bool) -> None:
    flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    for lang in ("vi", "en"):
        name = settings.collection(lang)
        if client.collection_exists(name):
            client.set_payload(name, payload={"is_latest": is_latest}, points=flt, wait=True)


# ---------------------------------------------------------------- ghi 1 tài liệu

def save_document(doc: Document, parents: list[ParentChunk], children: list[ChildChunk],
                  settings: Settings | None = None, *, force: bool = False, embed_cache: bool = True) -> dict:
    """→ {status: inserted|skipped, n_parents, n_points, latest_changes}."""
    s = settings or get_settings()
    client = qdrant(s)
    with pg_connect(s) as conn:
        existing = find_by_hash(conn, doc.file_hash)
        if existing and not force:
            return {"status": "skipped", "reason": "file_hash đã ingest", "doc_id": existing["doc_id"]}
    # embed trước khi ghi (lỗi API → không ghi gì)
    cache = EmbeddingCache() if embed_cache else None
    vectors: list[list[float] | None] = [None] * len(children)
    models = {}
    for lang in ("vi", "en"):
        idx = [i for i, c in enumerate(children) if c.lang == lang]
        if not idx:
            continue
        emb = get_embedder(lang, s)
        models[lang] = emb.model
        vecs = embed_children([children[i] for i in idx], emb, cache)
        for i, v in zip(idx, vecs):
            vectors[i] = v
    delete_points(client, s, doc.doc_id)
    n_points = upsert_children(client, s, children, vectors)  # type: ignore[arg-type]
    changes = []
    with pg_connect(s) as conn, conn.transaction():
        conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc.doc_id,))
        _insert_document(conn, doc)
        _insert_parents(conn, parents)
        if doc.doc_code:
            rows = conn.execute(
                "SELECT doc_id, revision, issue_date, ingested_at, is_latest FROM documents WHERE doc_code = %s",
                (doc.doc_code,)).fetchall()
            latest = max(rows, key=_version_key)["doc_id"]
            for r in rows:
                want = r["doc_id"] == latest
                if r["is_latest"] != want or r["doc_id"] == doc.doc_id:
                    conn.execute("UPDATE documents SET is_latest = %s WHERE doc_id = %s", (want, r["doc_id"]))
                    changes.append((r["doc_id"], want))
    for doc_id, want in changes:
        set_latest_payload(client, s, doc_id, want)
    return {"status": "inserted", "doc_id": doc.doc_id, "n_parents": len(parents), "n_points": n_points,
            "latest_changes": changes, "embedding_models": models}


def counts(settings: Settings | None = None, doc_id: str | None = None) -> dict:
    """Số bản ghi (để kiểm tra idempotent)."""
    s = settings or get_settings()
    client = qdrant(s)
    with pg_connect(s) as conn:
        where, args = ("WHERE doc_id = %s", (doc_id,)) if doc_id else ("", ())
        n_doc = conn.execute(f"SELECT count(*) AS n FROM documents {where}", args).fetchone()["n"]
        n_par = conn.execute(f"SELECT count(*) AS n FROM parent_chunks {where}", args).fetchone()["n"]
    flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]) if doc_id else None
    pts = {lang: client.count(s.collection(lang), count_filter=flt, exact=True).count for lang in ("vi", "en")}
    return {"documents": n_doc, "parent_chunks": n_par, **{f"points_{k}": v for k, v in pts.items()}}
