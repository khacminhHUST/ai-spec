"""Kết nối OAuth của từng user (docs/mcp-connectors.md mục 2.3): authorize_url → callback → token → kiểm tra kết nối."""
from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from app.services.agent.mcp.registry import ConnectorError, ConnectorRegistry
from app.services.agent.mcp.tools import ConnectorTools
from app.services.oauth import flow
from app.services.oauth.client_registry import ClientRegistry
from app.services.oauth.crypto import SecretBox
from app.services.oauth.errors import OAuthError
from app.services.oauth.store import OAuthStore
from app.services.oauth.token_store import TokenStore

log = logging.getLogger(__name__)

DISCONNECT_NOTE = ("Đã xóa token phía MEP. Access token cũ còn hiệu lực tối đa 60 phút ở server; "
                   "muốn thu hồi hẳn, ngắt ứng dụng ở Authorization Server (Server A).")


class OAuthConnect:
    def __init__(self, registry: ConnectorRegistry, store: OAuthStore, clients: ClientRegistry, tokens: TokenStore,
                 tools: ConnectorTools, box: Callable[[], SecretBox], http: Callable[[], httpx.Client],
                 redirect_uri: str, state_ttl_s: int):
        self.registry = registry
        self.store = store
        self.clients = clients
        self.tokens = tokens
        self.tools = tools
        self.box = box
        self.http = http
        self.redirect_uri = redirect_uri
        self.state_ttl_s = state_ttl_s

    def _oauth_row(self, connector_id: str) -> dict[str, Any]:
        row = self.registry.get(connector_id)
        if row is None:
            raise KeyError(connector_id)
        if row["auth_type"] != "oauth":
            raise ConnectorError("Connector không dùng OAuth – không cần kết nối")
        return row

    def connect(self, connector_id: str, user_id: str) -> str:
        """→ authorize_url (state + PKCE đã lưu oauth_pending)."""
        row = self._oauth_row(connector_id)
        if not row.get("enabled"):
            raise ConnectorError("Connector đang tắt")
        client = self.clients.ensure(row["issuer"], row["as_metadata"], self.redirect_uri)   # đổi redirect → DCR lại
        return flow.start(self.store, self.box(), user_id=user_id, connector_id=connector_id,
                          as_meta=row["as_metadata"], client=client, redirect_uri=self.redirect_uri,
                          resource=row.get("resource"), scope=row.get("scopes"), ttl_s=self.state_ttl_s)

    def callback(self, *, code: str | None, state: str | None, error: str | None,
                 user_hint: str | None = None) -> dict[str, Any]:
        """→ {connector_id, status, message}. status: connected | forbidden | needs_reauth | error."""
        cid: str | None = None
        try:
            pend = flow.consume_state(self.store, self.box(), state or "", user_id=user_hint)
            cid = pend.connector_id
            if error:
                return {"connector_id": cid, "status": "error", "message": flow.error_text(error)}
            if not code:
                return {"connector_id": cid, "status": "error", "message": "Thiếu mã code từ Authorization Server"}
            row = self._oauth_row(cid)
            client = self.clients.get(row["issuer"], self.redirect_uri)
            if client is None:
                return {"connector_id": cid, "status": "error", "message": "Chưa có client OAuth – bấm Kết nối lại"}
            tok = flow.exchange_code(self.http(), row["as_metadata"], client, code=code, redirect_uri=self.redirect_uri,
                                     code_verifier=pend.code_verifier, resource=row.get("resource"))
            self.tokens.save(pend.user_id, cid, tok)
            self.tools.invalidate(cid, pend.user_id)
            res = self.tools.probe(row, pend.user_id)
            if res["status"] == "error":       # token đã có, server tạm lỗi: vẫn coi là đã kết nối
                return {"connector_id": cid, "status": "connected", "message": f"Đã kết nối – {res['message']}"}
            return {"connector_id": cid, "status": res["status"],
                    "message": "Đã kết nối" if res["ok"] else res["message"]}
        except OAuthError as e:
            log.warning("OAuth callback lỗi: %s", e.code)
            msg = flow.error_text(e.code) if e.code in flow.ERROR_TEXT else e.message
            return {"connector_id": cid, "status": "error", "message": msg}
        except (KeyError, ConnectorError) as e:
            return {"connector_id": cid, "status": "error", "message": f"Connector không hợp lệ: {e}"}

    def disconnect(self, connector_id: str, user_id: str) -> str:
        self._oauth_row(connector_id)
        self.tokens.disconnect(user_id, connector_id)
        self.tools.invalidate(connector_id, user_id)
        return DISCONNECT_NOTE
