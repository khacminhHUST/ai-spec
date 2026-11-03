"""Lưu trữ connector MCP khai qua giao diện (bảng mcp_connectors, mcp_tool_overrides – migration 002_mcp.sql).

`ConnectorStore` là interface; `PgConnectorStore` là bản Postgres. Xóa connector → token, override, state đi kèm
bị xóa theo (ON DELETE CASCADE).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from psycopg.types.json import Jsonb

CONNECTOR_FIELDS = ("name", "display_name", "url", "transport", "auth_type", "headers_enc", "resource", "issuer",
                    "as_metadata", "scopes", "enabled", "created_by")


class ConnectorStore(ABC):
    @abstractmethod
    def list_connectors(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_connector(self, connector_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def insert_connector(self, row: dict[str, Any]) -> None:
        """Trùng `name` → ValueError."""

    @abstractmethod
    def update_connector(self, connector_id: str, **fields: Any) -> None: ...

    @abstractmethod
    def delete_connector(self, connector_id: str) -> None: ...

    @abstractmethod
    def get_overrides(self, connector_id: str) -> dict[str, str]:
        """tool_name (gốc) → read | write | disabled."""

    @abstractmethod
    def set_override(self, connector_id: str, tool_name: str, access: str | None) -> None:
        """access None → bỏ ghi đè."""


def _adapt(fields: dict[str, Any]) -> dict[str, Any]:
    bad = set(fields) - set(CONNECTOR_FIELDS)
    if bad:
        raise ValueError(f"cột connector không hợp lệ: {bad}")
    return {k: Jsonb(v) if k == "as_metadata" and v is not None else v for k, v in fields.items()}


class PgConnectorStore(ConnectorStore):
    def __init__(self, pool):
        self.pool = pool

    def _q(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            return list(cur) if cur.description else []

    def list_connectors(self):
        return self._q("SELECT * FROM mcp_connectors ORDER BY name")

    def get_connector(self, connector_id):
        rows = self._q("SELECT * FROM mcp_connectors WHERE connector_id = %s", (connector_id,))
        return rows[0] if rows else None

    def insert_connector(self, row):
        import psycopg

        fields = _adapt({k: v for k, v in row.items() if k != "connector_id"})
        cols = ["connector_id", *fields]
        try:
            self._q(f"INSERT INTO mcp_connectors ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    (row["connector_id"], *fields.values()))
        except psycopg.errors.UniqueViolation:
            raise ValueError(f"Tên connector đã tồn tại: {row.get('name')}") from None

    def update_connector(self, connector_id, **fields):
        import psycopg

        if not fields:
            return
        f = _adapt(fields)
        sets = ", ".join(f"{c} = %s" for c in f)
        try:
            self._q(f"UPDATE mcp_connectors SET {sets}, updated_at = now() WHERE connector_id = %s",
                    (*f.values(), connector_id))
        except psycopg.errors.UniqueViolation:
            raise ValueError(f"Tên connector đã tồn tại: {fields.get('name')}") from None

    def delete_connector(self, connector_id):
        self._q("DELETE FROM mcp_connectors WHERE connector_id = %s", (connector_id,))

    def get_overrides(self, connector_id):
        return {r["tool_name"]: r["access"] for r in
                self._q("SELECT tool_name, access FROM mcp_tool_overrides WHERE connector_id = %s", (connector_id,))}

    def set_override(self, connector_id, tool_name, access):
        if access is None:
            self._q("DELETE FROM mcp_tool_overrides WHERE connector_id = %s AND tool_name = %s", (connector_id, tool_name))
            return
        self._q("INSERT INTO mcp_tool_overrides (connector_id, tool_name, access) VALUES (%s,%s,%s) "
                "ON CONFLICT (connector_id, tool_name) DO UPDATE SET access = EXCLUDED.access",
                (connector_id, tool_name, access))
