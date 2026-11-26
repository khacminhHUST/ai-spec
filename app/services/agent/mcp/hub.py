"""McpHub: ghép registry + OAuth + tool theo user cho API (`app/api/routes/mcp.py`) và danh mục nguồn của agent.

Tham số đọc từ config/mcp.yaml: public_base_url, client_name, tool_cache_ttl_s, refresh_skew_s, http_timeout_s,
allow_http_localhost, state_ttl_s. Secret: MEP_SECRET_KEY (.env) – thiếu thì OAuth/header tĩnh báo lỗi rõ ràng,
connector `none` vẫn chạy. Mọi hàm theo user_id chỉ thấy connector dùng chung + connector cá nhân của user đó.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import httpx
from langchain_core.tools import BaseTool

from app.services.agent.mcp.catalog import NOT_CONNECTED, READY, STATUS_TEXT, USABLE, SourceUnavailable, source_entry
from app.services.agent.mcp.connect import OAuthConnect
from app.services.agent.mcp.describe import suggest_description
from app.services.agent.mcp.oauth.client_registry import ClientRegistry
from app.services.agent.mcp.oauth.crypto import SecretBox, secret_box
from app.services.agent.mcp.oauth.store import OAuthStore
from app.services.agent.mcp.oauth.token_store import TokenStore
from app.services.agent.mcp.registry import YAML_PREFIX, ConnectorRegistry
from app.services.agent.mcp.store import ConnectorStore
from app.services.agent.mcp.tools import ConnectorTools

log = logging.getLogger(__name__)


class McpHub:
    def __init__(self, cfg: dict[str, Any], oauth_store: OAuthStore, connector_store: ConnectorStore, *,
                 box: Callable[[], SecretBox] | None = None, http: httpx.Client | None = None, llm: Any = None):
        """llm: model nhanh để tóm tắt mô tả connector từ danh sách tool (None → dùng danh sách tên tool)."""
        self.cfg = cfg
        self.llm = llm
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
                                    timeout_s=float(cfg.get("http_timeout_s", 20)),
                                    describe=lambda row, listing: suggest_description(row, listing, self.llm))
        self.oauth = OAuthConnect(self.registry, oauth_store, self.clients, self.tokens, self.tools, self.box,
                                  self.http, self.redirect_uri, int(cfg.get("state_ttl_s", 600)))

    @classmethod
    def default(cls, settings, pool=None, llm=None) -> "McpHub":
        from app.db.client import pg_pool
        from app.services.agent.config import mcp_cfg
        from app.services.agent.mcp.store import PgConnectorStore
        from app.services.agent.mcp.oauth.store import PgOAuthStore

        p = pool or pg_pool(settings)
        return cls(mcp_cfg(), PgOAuthStore(p), PgConnectorStore(p), llm=llm)

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
    def connector_view(self, row: dict[str, Any], statuses: dict[str, str], user_id: str = "",
                       is_admin: bool = False) -> dict[str, Any]:
        """Không bao giờ trả giá trị header / token – chỉ tên header. can_manage: user sửa/xóa/đặt quyền tool được."""
        v = {k: row.get(k) for k in ("connector_id", "name", "display_name", "description", "url", "transport", "auth_type",
                                      "enabled", "resource", "issuer", "scopes", "created_by", "created_at",
                                      "updated_at")}
        v.update(source="db", readonly=False, shared=bool(row.get("shared")),
                 can_manage=self.registry.can_manage(row, user_id or row.get("created_by", ""), is_admin))
        try:
            v["header_names"] = self.registry.header_names(row) if row["auth_type"] == "headers" else []
        except Exception:  # noqa: BLE001 – thiếu MEP_SECRET_KEY
            v["header_names"] = []
        v["status"] = statuses.get(row["connector_id"], NOT_CONNECTED) if row["auth_type"] == "oauth" else READY
        v["registration"] = bool((row.get("as_metadata") or {}).get("registration_endpoint"))
        return v

    def list_connectors(self, user_id: str, is_admin: bool = False) -> list[dict[str, Any]]:
        statuses = self.tokens.store.token_statuses(user_id)
        out = [self.connector_view(r, statuses, user_id, is_admin) for r in self.registry.list_db(user_id)]
        out += [{**y, "status": READY} for y in self.registry.yaml_connectors()]
        return out

    def invalidate(self, connector_id: str, user_id: str | None = None) -> None:
        self.tools.invalidate(connector_id, user_id)

    def suggest_description(self, row: dict[str, Any], user_id: str) -> str:
        """Gợi ý mô tả (không lưu): giới thiệu server tự trả → LLM tóm tắt danh sách tool → tên tool."""
        return suggest_description(row, self.tools.listing(row, user_id), self.llm)

    # ---------------------------------------------------------------- dùng cho agent (mcp/catalog.py)
    def catalog(self, user_id: str) -> list[dict[str, str]]:
        """Connector đang bật + trạng thái với user. Chỉ đọc DB."""
        rows = [r for r in self.registry.usable(user_id) if r.get("enabled")]
        statuses = self.tokens.store.token_statuses(user_id) if any(r["auth_type"] == "oauth" for r in rows) else {}
        return [source_entry(r["name"], r.get("display_name") or r["name"], r.get("description") or "",
                             statuses.get(r["connector_id"], NOT_CONNECTED) if r["auth_type"] == "oauth" else READY)
                for r in rows]

    def source_tools(self, user_id: str, name: str) -> list[BaseTool]:
        row = next((r for r in self.registry.usable(user_id) if r["name"] == name and r.get("enabled")), None)
        if row is None:
            raise SourceUnavailable(f"Không có nguồn '{name}' (hoặc nguồn đang tắt)")
        label = row.get("display_name") or name
        if row["auth_type"] == "oauth":
            status = self.tokens.store.token_statuses(user_id).get(row["connector_id"], NOT_CONNECTED)
            if status not in USABLE:
                raise SourceUnavailable(f"{label}: {STATUS_TEXT.get(status, status)}")
        try:
            return self.tools.connector_tools(row, user_id)
        except Exception as e:  # noqa: BLE001
            _, msg = self.tools.explain(row, e)
            log.warning("Nạp tool connector %s lỗi: %s", name, msg)
            raise SourceUnavailable(msg) from None

    @staticmethod
    def is_yaml(connector_id: str) -> bool:
        return connector_id.startswith(YAML_PREFIX)
