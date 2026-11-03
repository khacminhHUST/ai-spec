"""Lưu trữ OAuth (bảng oauth_clients, oauth_pending, mcp_user_tokens – migration 002_mcp.sql).

`OAuthStore` là interface; `PgOAuthStore` là bản Postgres (pool autocommit, dict_row). Unit test dùng bản in-memory
(tests/agent/unit/mcp/fakes.py). Mọi cột *_enc đã mã hóa ở tầng trên – store không thấy plaintext.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Callable, Iterator

TOKEN_FIELDS = ("access_token_enc", "refresh_token_enc", "expires_at", "scope", "status")
# (row hiện tại | None, hàm cập nhật **fields) – trong khóa theo (user_id, connector_id)
Locked = tuple[dict[str, Any] | None, Callable[..., None]]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class OAuthStore(ABC):
    # ---- oauth_clients ----
    @abstractmethod
    def get_client(self, issuer: str, redirect_uri: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_client(self, issuer: str, redirect_uri: str, client_id: str, client_secret_enc: str | None,
                    auth_method: str) -> None: ...

    # ---- oauth_pending ----
    @abstractmethod
    def add_pending(self, state: str, user_id: str, connector_id: str, code_verifier_enc: str,
                    expires_at: dt.datetime) -> None:
        """Thêm state mới (và dọn state đã hết hạn)."""

    @abstractmethod
    def pop_pending(self, state: str) -> dict[str, Any] | None:
        """Đọc và XÓA state (dùng 1 lần) – kể cả khi đã hết hạn."""

    # ---- mcp_user_tokens ----
    @abstractmethod
    def get_token(self, user_id: str, connector_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_token(self, user_id: str, connector_id: str, **fields: Any) -> None:
        """Upsert các cột trong TOKEN_FIELDS."""

    @abstractmethod
    def delete_token(self, user_id: str, connector_id: str) -> None: ...

    @abstractmethod
    def token_statuses(self, user_id: str) -> dict[str, str]:
        """connector_id → status của user."""

    @abstractmethod
    @contextmanager
    def lock_token(self, user_id: str, connector_id: str) -> Iterator[Locked]:
        """Khóa dòng token (SELECT … FOR UPDATE) trong 1 transaction; luồng khác chờ tới khi thoát."""


def _check_fields(fields: dict[str, Any]) -> None:
    bad = set(fields) - set(TOKEN_FIELDS)
    if bad:
        raise ValueError(f"cột token không hợp lệ: {bad}")


class PgOAuthStore(OAuthStore):
    def __init__(self, pool):
        self.pool = pool

    def _q(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            return list(cur) if cur.description else []

    def get_client(self, issuer, redirect_uri):
        rows = self._q("SELECT * FROM oauth_clients WHERE issuer = %s AND redirect_uri = %s", (issuer, redirect_uri))
        return rows[0] if rows else None

    def save_client(self, issuer, redirect_uri, client_id, client_secret_enc, auth_method):
        self._q("INSERT INTO oauth_clients (issuer, redirect_uri, client_id, client_secret_enc, auth_method) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (issuer, redirect_uri) DO UPDATE SET "
                "client_id = EXCLUDED.client_id, client_secret_enc = EXCLUDED.client_secret_enc, "
                "auth_method = EXCLUDED.auth_method, registered_at = now()",
                (issuer, redirect_uri, client_id, client_secret_enc, auth_method))

    def add_pending(self, state, user_id, connector_id, code_verifier_enc, expires_at):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM oauth_pending WHERE expires_at < now()")
            conn.execute("INSERT INTO oauth_pending (state, user_id, connector_id, code_verifier_enc, expires_at) "
                         "VALUES (%s,%s,%s,%s,%s)", (state, user_id, connector_id, code_verifier_enc, expires_at))

    def pop_pending(self, state):
        rows = self._q("DELETE FROM oauth_pending WHERE state = %s RETURNING *", (state,))
        return rows[0] if rows else None

    def get_token(self, user_id, connector_id):
        rows = self._q("SELECT * FROM mcp_user_tokens WHERE user_id = %s AND connector_id = %s", (user_id, connector_id))
        return rows[0] if rows else None

    def save_token(self, user_id, connector_id, **fields):
        _check_fields(fields)
        cols = list(fields)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        self._q(f"INSERT INTO mcp_user_tokens (user_id, connector_id, {', '.join(cols)}) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) "
                f"ON CONFLICT (user_id, connector_id) DO UPDATE SET {sets}, updated_at = now()",
                (user_id, connector_id, *fields.values()))

    def delete_token(self, user_id, connector_id):
        self._q("DELETE FROM mcp_user_tokens WHERE user_id = %s AND connector_id = %s", (user_id, connector_id))

    def token_statuses(self, user_id):
        return {r["connector_id"]: r["status"]
                for r in self._q("SELECT connector_id, status FROM mcp_user_tokens WHERE user_id = %s", (user_id,))}

    @contextmanager
    def lock_token(self, user_id, connector_id):
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT * FROM mcp_user_tokens WHERE user_id = %s AND connector_id = %s FOR UPDATE",
                               (user_id, connector_id)).fetchone()

            def update(**fields: Any) -> None:
                _check_fields(fields)
                sets = ", ".join(f"{c} = %s" for c in fields)
                conn.execute(f"UPDATE mcp_user_tokens SET {sets}, updated_at = now() "
                             f"WHERE user_id = %s AND connector_id = %s", (*fields.values(), user_id, connector_id))

            yield row, update
