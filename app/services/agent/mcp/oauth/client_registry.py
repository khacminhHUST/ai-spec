"""Đăng ký client OAuth (DCR, RFC 7591) – mỗi (issuer, redirect_uri) chỉ 1 lần (A giới hạn 10 lần/giờ/IP).

AS không có `registration_endpoint` → người dùng nhập client_id (+ client_secret) ở mục "Nâng cao".
Có secret → `client_secret_post` nếu AS hỗ trợ, ngược lại `client_secret_basic` (mặc định RFC 8414).
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.services.agent.mcp.oauth.crypto import SecretBox
from app.services.agent.mcp.oauth.errors import OAuthError
from app.services.agent.mcp.oauth.store import OAuthStore

log = logging.getLogger(__name__)


AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")


@dataclass(frozen=True)
class ClientInfo:
    client_id: str
    client_secret: str | None
    auth_method: str            # none | client_secret_post | client_secret_basic


def secret_auth_method(as_meta: dict[str, Any]) -> str:
    supported = as_meta.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    return "client_secret_post" if "client_secret_post" in supported else "client_secret_basic"


class ClientRegistry:
    def __init__(self, store: OAuthStore, box: Callable[[], SecretBox], http: Callable[[], httpx.Client],
                 client_name: str):
        self.store = store
        self.box = box
        self.http = http
        self.client_name = client_name
        self._lock = threading.Lock()

    def get(self, issuer: str, redirect_uri: str) -> ClientInfo | None:
        row = self.store.get_client(issuer, redirect_uri)
        if not row:
            return None
        secret = self.box().decrypt(row["client_secret_enc"]) if row.get("client_secret_enc") else None
        return ClientInfo(row["client_id"], secret, row["auth_method"])

    def save_manual(self, issuer: str, as_meta: dict[str, Any], redirect_uri: str, client_id: str,
                    client_secret: str | None) -> ClientInfo:
        client_id = (client_id or "").strip()
        if not client_id:
            raise OAuthError("client_id_required", "Thiếu client_id")
        secret = (client_secret or "").strip() or None
        method = secret_auth_method(as_meta) if secret else "none"
        self.store.save_client(issuer, redirect_uri, client_id, self.box().encrypt(secret) if secret else None, method)
        return ClientInfo(client_id, secret, method)

    def ensure(self, issuer: str, as_meta: dict[str, Any], redirect_uri: str) -> ClientInfo:
        """Client đã lưu → dùng lại; chưa có → DCR (1 lần, có khóa trong tiến trình)."""
        with self._lock:
            found = self.get(issuer, redirect_uri)
            if found:
                return found
            endpoint = as_meta.get("registration_endpoint")
            if not endpoint:
                raise OAuthError("client_id_required", "Authorization Server không hỗ trợ đăng ký tự động (DCR) – "
                                                       "nhập client_id ở mục Nâng cao")
            return self._register(issuer, as_meta, endpoint, redirect_uri)

    def _register(self, issuer: str, as_meta: dict[str, Any], endpoint: str, redirect_uri: str) -> ClientInfo:
        body = {"client_name": self.client_name, "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                "token_endpoint_auth_method": "none"}
        try:
            r = self.http().post(endpoint, json=body, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise OAuthError("registration_failed", f"Không gọi được registration_endpoint: {type(e).__name__}") from None
        if r.status_code == 429:
            raise OAuthError("registration_failed", "Authorization Server giới hạn số lần đăng ký (429) – thử lại sau")
        try:
            data = json.loads(r.content)
        except ValueError:
            data = {}
        if r.status_code not in (200, 201) or not isinstance(data, dict) or not data.get("client_id"):
            detail = data.get("error") or data.get("detail") if isinstance(data, dict) else None
            raise OAuthError("registration_failed", f"Đăng ký client lỗi HTTP {r.status_code}: {detail or ''}".strip())
        secret = data.get("client_secret") or None
        method = data.get("token_endpoint_auth_method") or ("client_secret_basic" if secret else "none")
        if method not in AUTH_METHODS or (secret and method == "none"):
            method = secret_auth_method(as_meta) if secret else "none"
        self.store.save_client(issuer, redirect_uri, data["client_id"],
                               self.box().encrypt(secret) if secret else None, method)
        log.info("Đã đăng ký client OAuth với %s", issuer)
        return ClientInfo(data["client_id"], secret, method)
