"""DB clients: Postgres (psycopg3, search_path theo namespace) và Qdrant."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from qdrant_client import QdrantClient

from app.core.config import Settings, get_settings


def _options(s: Settings) -> str:
    return f"-c search_path={s.pg_schema},public"


def pg_connect(settings: Settings | None = None, *, autocommit: bool = False, dict_rows: bool = True) -> psycopg.Connection:
    s = settings or get_settings()
    return psycopg.connect(
        s.postgres_url,
        options=_options(s),
        autocommit=autocommit,
        row_factory=dict_row if dict_rows else psycopg.rows.tuple_row,
    )


def pg_pool(settings: Settings | None = None, **kwargs):
    """Connection pool (dùng cho Query API)."""
    from psycopg_pool import ConnectionPool

    s = settings or get_settings()
    return ConnectionPool(
        s.postgres_url,
        kwargs={"options": _options(s), "row_factory": dict_row, "autocommit": True, "prepare_threshold": 0},
        open=True,
        **kwargs,
    )


def checkpointer_conn(settings: Settings | None = None) -> psycopg.Connection:
    """Kết nối cho LangGraph PostgresSaver (yêu cầu autocommit + dict_row)."""
    s = settings or get_settings()
    return psycopg.connect(
        s.postgres_url, options=_options(s), autocommit=True, prepare_threshold=0, row_factory=dict_row
    )


def qdrant(settings: Settings | None = None) -> QdrantClient:
    s = settings or get_settings()
    return QdrantClient(url=s.qdrant_url, timeout=30)
