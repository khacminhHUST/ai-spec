"""Migration: bảng Postgres + collection Qdrant + checkpoint LangGraph + seed project_aliases.

CLI: python -m app.cli.migrate
"""
from __future__ import annotations

import logging
from pathlib import Path

from qdrant_client import models as qm

from app.core.config import Settings, get_settings
from app.db.client import checkpointer_conn, pg_connect, qdrant
from app.utils.keys import normalize_alias

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
KEYWORD_INDEX_FIELDS = ("project_id", "building", "stream", "disciplines", "doc_id", "revision", "lang",
                        "child_id", "parent_id", "section_key")
BOOL_INDEX_FIELDS = ("is_latest",)


def migrate_postgres(s: Settings) -> list[str]:
    applied: list[str] = []
    with pg_connect(s, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{s.pg_schema}"')
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
    return applied


def setup_checkpointer(s: Settings) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    with checkpointer_conn(s) as conn:
        PostgresSaver(conn).setup()


def alias_rows(s: Settings) -> list[tuple[str, str, str, str, str]]:
    """(alias_norm, alias, project_id, building, kind) từ config/projects.yaml."""
    rows: dict[tuple[str, str, str], tuple] = {}

    def add(alias: str, pid: str, building: str, kind: str) -> None:
        norm = normalize_alias(alias)
        if norm:
            rows.setdefault((norm, pid, building), (norm, alias, pid, building, kind))

    for p in s.project_list():
        pid = p["project_id"]
        for a in [p["project_id"], p["name"], *p.get("aliases", [])]:
            add(a, pid, "", "project")
        for a in p.get("owner_aliases", []):
            add(a, pid, "", "owner")
        for b in p.get("buildings", []):
            for a in [b["building"], *b.get("aliases", [])]:
                add(a, pid, b["building"], "building")
    return list(rows.values())


def seed_aliases(s: Settings) -> int:
    rows = alias_rows(s)
    with pg_connect(s, autocommit=True) as conn, conn.transaction():
        conn.execute("DELETE FROM project_aliases")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO project_aliases (alias_norm, alias, project_id, building, kind) VALUES (%s,%s,%s,%s,%s)", rows
            )
    return len(rows)


def migrate_qdrant(s: Settings, recreate: bool = False) -> list[str]:
    client = qdrant(s)
    created = []
    for lang in ("vi", "en"):
        name, dim = s.collection(lang), int(s.embedding_cfg(lang)["dimensions"])
        if client.collection_exists(name):
            cur_dim = client.get_collection(name).config.params.vectors["dense"].size
            if not recreate and cur_dim == dim:
                continue
            if not recreate:
                raise RuntimeError(f"{name}: dim hiện tại {cur_dim} ≠ config {dim} → chạy lại với --recreate")
            client.delete_collection(name)
        client.create_collection(
            name,
            vectors_config={"dense": qm.VectorParams(size=dim, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
        for f in KEYWORD_INDEX_FIELDS:
            client.create_payload_index(name, f, qm.PayloadSchemaType.KEYWORD)
        for f in BOOL_INDEX_FIELDS:
            client.create_payload_index(name, f, qm.PayloadSchemaType.BOOL)
        created.append(name)
    return created


def migrate(s: Settings | None = None, recreate: bool = False) -> dict:
    s = s or get_settings()
    result = {
        "postgres": migrate_postgres(s),
        "qdrant": migrate_qdrant(s, recreate),
    }
    setup_checkpointer(s)
    result["aliases"] = seed_aliases(s)
    return result


def drop(s: Settings) -> None:
    if s.pg_schema == "public":
        raise RuntimeError("Không drop namespace public")
    with pg_connect(s, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{s.pg_schema}" CASCADE')
    client = qdrant(s)
    for lang in ("vi", "en"):
        if client.collection_exists(s.collection(lang)):
            client.delete_collection(s.collection(lang))
