"""Token của từng (user, connector): lưu mã hóa, lấy access token, refresh CÓ KHÓA (docs/mcp-connectors.md mục 2.4).

A xoay vòng refresh token và phát hiện dùng lại: hai luồng cùng gửi 1 refresh token → A thu hồi cả phiên.
Vì vậy refresh chạy trong `lock_token` (SELECT … FOR UPDATE); có khóa rồi đọc lại – luồng khác đã refresh xong thì
dùng luôn token mới, không gọi token endpoint lần nữa. Refresh token mới được lưu ngay trong cùng transaction.
Không bao giờ log token.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable

import httpx

from app.services.agent.mcp.oauth.client_registry import ClientInfo, ClientRegistry
from app.services.agent.mcp.oauth.crypto import SecretBox
from app.services.agent.mcp.oauth.errors import OAuthError, ReauthRequired
from app.services.agent.mcp.oauth.flow import TokenSet, refresh_grant
from app.services.agent.mcp.oauth.store import OAuthStore, utcnow

log = logging.getLogger(__name__)

CONNECTED, FORBIDDEN, NEEDS_REAUTH = "connected", "forbidden", "needs_reauth"
# Lỗi refresh nghĩa là phiên không dùng được nữa (khác lỗi mạng tạm thời)
FATAL_REFRESH = {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_target"}


class TokenStore:
    def __init__(self, store: OAuthStore, box: Callable[[], SecretBox], http: Callable[[], httpx.Client],
                 clients: ClientRegistry, redirect_uri: str, *, skew_s: float = 60,
                 now: Callable[[], dt.datetime] = utcnow):
        self.store = store
        self.box = box
        self.http = http
        self.clients = clients
        self.redirect_uri = redirect_uri
        self.skew = dt.timedelta(seconds=skew_s)
        self.now = now

    # ---------------------------------------------------------------- lưu / trạng thái
    def save(self, user_id: str, connector_id: str, ts: TokenSet, status: str = CONNECTED) -> None:
        b = self.box()
        self.store.save_token(user_id, connector_id, access_token_enc=b.encrypt(ts.access_token),
                              refresh_token_enc=b.encrypt(ts.refresh_token) if ts.refresh_token else None,
                              expires_at=ts.expires_at(self.now()), scope=ts.scope, status=status)

    def status(self, user_id: str, connector_id: str) -> str | None:
        row = self.store.get_token(user_id, connector_id)
        return row["status"] if row else None

    def set_status(self, user_id: str, connector_id: str, status: str) -> None:
        with self.store.lock_token(user_id, connector_id) as (row, update):
            if row is not None and row["status"] != status:
                update(status=status)

    def disconnect(self, user_id: str, connector_id: str) -> None:
        self.store.delete_token(user_id, connector_id)

    # ---------------------------------------------------------------- lấy / refresh
    def _fresh(self, row: dict[str, Any]) -> bool:
        exp = row.get("expires_at")
        return exp is None or exp - self.now() > self.skew

    def access_token(self, user_id: str, connector: dict[str, Any]) -> str:
        """Access token còn hạn (> refresh_skew_s); sắp hết → refresh có khóa. Không có / cần kết nối lại → ReauthRequired."""
        row = self.store.get_token(user_id, connector["connector_id"])
        if row is None or row["status"] == NEEDS_REAUTH:
            raise ReauthRequired()
        if self._fresh(row):
            return self.box().decrypt(row["access_token_enc"])
        return self.refresh(user_id, connector)

    def refresh(self, user_id: str, connector: dict[str, Any], *, rejected: str | None = None) -> str:
        """rejected = access token vừa bị server trả 401 → refresh dù chưa tới hạn, trừ khi luồng khác đã thay token."""
        b = self.box()
        client = self.clients.get(connector["issuer"], self.redirect_uri)   # đọc trước khi giữ khóa (không lồng kết nối DB)
        outcome: Exception
        with self.store.lock_token(user_id, connector["connector_id"]) as (row, update):
            if row is None or row["status"] == NEEDS_REAUTH:
                outcome = ReauthRequired()
            else:
                current = b.decrypt(row["access_token_enc"])
                if rejected is None and self._fresh(row):
                    return current                       # luồng khác vừa refresh xong
                if rejected is not None and current != rejected:
                    return current
                result = self._do_refresh(b, row, connector, client)
                if isinstance(result, TokenSet):
                    update(access_token_enc=b.encrypt(result.access_token),
                           refresh_token_enc=(b.encrypt(result.refresh_token) if result.refresh_token
                                              else row.get("refresh_token_enc")),
                           expires_at=result.expires_at(self.now()), scope=result.scope or row.get("scope"),
                           status=CONNECTED)
                    return result.access_token
                if isinstance(result, ReauthRequired):
                    update(status=NEEDS_REAUTH)          # ghi trạng thái trước khi thoát transaction
                outcome = result
        raise outcome

    def _do_refresh(self, b: SecretBox, row: dict[str, Any], connector: dict[str, Any],
                    client: ClientInfo | None) -> TokenSet | OAuthError:
        if not row.get("refresh_token_enc"):
            return ReauthRequired("Không có refresh token – cần kết nối lại")
        if client is None:
            return ReauthRequired("Chưa có client OAuth cho Authorization Server – cần kết nối lại")
        try:
            return refresh_grant(self.http(), connector["as_metadata"], client,
                                 refresh_token=b.decrypt(row["refresh_token_enc"]), resource=connector.get("resource"))
        except OAuthError as e:
            log.warning("Refresh token connector %s lỗi: %s", connector.get("name") or connector["connector_id"], e.code)
            if e.code in FATAL_REFRESH:
                return ReauthRequired()
            return e
