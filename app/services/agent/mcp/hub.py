"""McpHub: ghép registry + OAuth + tool theo user cho API (`app/api/routes/mcp.py`) và vòng lặp agent (runtime).

Tham số đọc từ config/mcp.yaml: public_base_url, client_name, tool_cache_ttl_s, refresh_skew_s, http_timeout_s,
allow_http_localhost, state_ttl_s. Secret: MEP_SECRET_KEY (.env) – thiếu thì OAuth/header tĩnh báo lỗi rõ ràng,
connector `none` vẫn chạy.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import httpx
from langchain_core.tools import BaseTool

from app.services.agent.mcp.connect import OAuthConnect
from app.services.agent.mcp.registry import YAML_PREFIX, ConnectorRegistry
from app.services.agent.mcp.store import ConnectorStore
from app.services.agent.mcp.tools import ConnectorTools
from app.services.oauth.client_registry import ClientRegistry
from app.services.oauth.crypto import SecretBox, secret_box
from app.services.oauth.store import OAuthStore
from app.services.oauth.token_store import TokenStore

log = logging.getLogger(__name__)

NOT_CONNECTED, READY = "not_connected", "ready"


class McpHub:
    def __init__(self, cfg: dict[str, Any], oauth_store: OAuthStore, connector_store: ConnectorStore, *,
                 box: Callable[[], SecretBox] | None = None, http: httpx.Client | None = None):
        self.cfg = cfg
        self.redirect_uri = str(cfg.get("public_base_url") or "http://localhost:5173").rstrip("/") + "/mcp/oauth/callback"
        self._box_factory = box or secret_box
        self._box: SecretBox | None = None
        self._http = http
        self.clients = ClientRegistry(oauth_store, self.box, self.http, cfg.get("client_name", "MEP Project Assistant"))
        self.tokens = TokenStore(oauth_store, self.box, self.http, self.clients, self.redirect_uri,
                                 skew_s=float(cfg.get("refresh_skew_s", 60)))
        self.registry = ConnectorRegistry(connector_store, oauth_store, self.clients, self.box, self.http, cfg,
                                          self.redirect_uri)
        self.tools = ConnectorTools(self.registry, self.tokens, ttl_s=float(cfg.get("tool_cache_ttl_s", 600)),
                                    timeout_s=float(cfg.get("http_timeout_s", 20)))
        self.oauth = OAuthConnect(self.registry, oauth_store, self.clients, self.tokens, self.tools, self.box,
                                  self.http, self.redirect_uri, int(cfg.get("state_ttl_s", 600)))

    @classmethod
    def default(cls, settings, pool=None) -> "McpHub":
        from app.db.client import pg_pool
        from app.services.agent.config import mcp_cfg
        from app.services.agent.mcp.store import PgConnectorStore
        from app.services.oauth.store import PgOAuthStore

        p = pool or pg_pool(settings)
        return cls(mcp_cfg(), PgOAuthStore(p), PgConnectorStore(p))

    # ---------------------------------------------------------------- tài nguyên dùng chung
    def box(self) -> SecretBox:
        """Khóa mã hóa (tạo 1 lần). Thiếu MEP_SECRET_KEY → SecretKeyMissing (thử lại ở lần gọi sau)."""
        if self._box is None:
            self._box = self._box_factory()
        return self._box

    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=float(self.cfg.get("http_timeout_s", 20)), follow_redirects=False)
        return self._http

    # ---------------------------------------------------------------- dùng cho API
    def connector_view(self, row: dict[str, Any], statuses: dict[str, str]) -> dict[str, Any]:
        """Không bao giờ trả giá trị header / token – chỉ tên header."""
        v = {k: row.get(k) for k in ("connector_id", "name", "display_name", "url", "transport", "auth_type",
                                      "enabled", "resource", "issuer", "scopes", "created_by", "created_at",
                                      "updated_at")}
        v.update(source="db", readonly=False)
        try:
            v["header_names"] = self.registry.header_names(row) if row["auth_type"] == "headers" else []
        except Exception:  # noqa: BLE001 – thiếu MEP_SECRET_KEY
            v["header_names"] = []
        v["status"] = statuses.get(row["connector_id"], NOT_CONNECTED) if row["auth_type"] == "oauth" else READY
        v["registration"] = bool((row.get("as_metadata") or {}).get("registration_endpoint"))
        return v

    def list_connectors(self, user_id: str) -> list[dict[str, Any]]:
        statuses = self.tokens.store.token_statuses(user_id)
        out = [self.connector_view(r, statuses) for r in self.registry.list_db()]
        out += [{**y, "status": READY} for y in self.registry.yaml_connectors()]
        return out

    def invalidate(self, connector_id: str, user_id: str | None = None) -> None:
        self.tools.invalidate(connector_id, user_id)

    # ---------------------------------------------------------------- dùng cho agent
    def turn_tools(self, user_id: str) -> list[BaseTool]:
        try:
            return self.tools.turn_tools(user_id)
        except Exception as e:  # noqa: BLE001 – lượt chat vẫn chạy khi phần connector lỗi
            log.error("Nạp tool connector lỗi: %s", type(e).__name__)
            return []

    @staticmethod
    def is_yaml(connector_id: str) -> bool:
        return connector_id.startswith(YAML_PREFIX)
