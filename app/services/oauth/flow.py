"""Luồng authorization code + PKCE (docs/mcp-connectors.md mục 2.3): authorize_url, state dùng 1 lần, gọi token endpoint.

Lỗi token endpoint: chuẩn RFC 6749 `{"error": ...}` hoặc kiểu A (FastAPI) `{"detail": "invalid_grant"}` → OAuthError(code).
Không log body thành công (chứa token).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import httpx

from app.services.oauth.client_registry import ClientInfo
from app.services.oauth.crypto import SecretBox
from app.services.oauth.errors import OAuthError
from app.services.oauth.pkce import challenge_s256, new_state, new_verifier
from app.services.oauth.store import OAuthStore, utcnow

ERROR_TEXT = {
    "invalid_target": "Server MCP (resource) chưa được quản trị Authorization Server đăng ký – liên hệ quản trị Server A",
    "invalid_grant": "Mã/refresh token không hợp lệ hoặc đã bị thu hồi – cần kết nối lại",
    "access_denied": "Người dùng từ chối cấp quyền",
}


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str | None

    def __repr__(self) -> str:      # không lộ token khi bị in/log
        return f"TokenSet(expires_in={self.expires_in}, scope={self.scope!r})"

    def expires_at(self, now: dt.datetime | None = None) -> dt.datetime | None:
        return (now or utcnow()) + dt.timedelta(seconds=self.expires_in) if self.expires_in else None


@dataclass
class Pending:
    user_id: str
    connector_id: str
    code_verifier: str


def error_text(code: str) -> str:
    return ERROR_TEXT.get(code, code)


def authorize_url(as_meta: dict[str, Any], client: ClientInfo, redirect_uri: str, state: str, challenge: str,
                  resource: str | None, scope: str | None) -> str:
    params = {"response_type": "code", "client_id": client.client_id, "redirect_uri": redirect_uri, "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    if resource:
        params["resource"] = resource
    if scope:
        params["scope"] = scope
    u = urlparse(as_meta["authorization_endpoint"])
    query = urlencode([*parse_qsl(u.query), *params.items()])
    return urlunparse(u._replace(query=query))


def start(store: OAuthStore, box: SecretBox, *, user_id: str, connector_id: str, as_meta: dict[str, Any],
          client: ClientInfo, redirect_uri: str, resource: str | None, scope: str | None, ttl_s: int) -> str:
    """Sinh state + PKCE, lưu oauth_pending (verifier mã hóa). → authorize_url."""
    state, verifier = new_state(), new_verifier()
    store.add_pending(state, user_id, connector_id, box.encrypt(verifier), utcnow() + dt.timedelta(seconds=ttl_s))
    return authorize_url(as_meta, client, redirect_uri, state, challenge_s256(verifier), resource, scope)


def consume_state(store: OAuthStore, box: SecretBox, state: str, *, user_id: str | None = None) -> Pending:
    """state phải tồn tại, chưa dùng, chưa hết hạn; xóa ngay khi đọc. user_id (nếu biết) phải khớp."""
    row = store.pop_pending(state or "")
    if not row:
        raise OAuthError("invalid_state", "Phiên kết nối không hợp lệ hoặc đã dùng – bấm Kết nối lại")
    if row["expires_at"] < utcnow():
        raise OAuthError("invalid_state", "Phiên kết nối đã hết hạn – bấm Kết nối lại")
    if user_id is not None and user_id != row["user_id"]:
        raise OAuthError("invalid_state", "Phiên kết nối thuộc người dùng khác")
    return Pending(row["user_id"], row["connector_id"], box.decrypt(row["code_verifier_enc"]))


def token_request(http: httpx.Client, as_meta: dict[str, Any], client: ClientInfo, data: dict[str, str]) -> TokenSet:
    data = {**data, "client_id": client.client_id}
    auth = None
    if client.client_secret and client.auth_method == "client_secret_post":
        data["client_secret"] = client.client_secret
    elif client.client_secret and client.auth_method == "client_secret_basic":
        auth = httpx.BasicAuth(client.client_id, client.client_secret)
    try:
        r = http.post(as_meta["token_endpoint"], data=data, auth=auth, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise OAuthError("token_endpoint_unreachable", f"Không gọi được token endpoint: {type(e).__name__}") from None
    try:
        body = json.loads(r.content)
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        code = body.get("error") or (body.get("detail") if isinstance(body.get("detail"), str) else None)
        code = code or f"http_{r.status_code}"
        raise OAuthError(code, error_text(code))
    expires = body.get("expires_in")
    return TokenSet(str(body["access_token"]), body.get("refresh_token") or None,
                    int(expires) if expires is not None else None, body.get("scope"))


def exchange_code(http: httpx.Client, as_meta: dict[str, Any], client: ClientInfo, *, code: str, redirect_uri: str,
                  code_verifier: str, resource: str | None) -> TokenSet:
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": code_verifier}
    if resource:
        data["resource"] = resource
    return token_request(http, as_meta, client, data)


def refresh_grant(http: httpx.Client, as_meta: dict[str, Any], client: ClientInfo, *, refresh_token: str,
                  resource: str | None) -> TokenSet:
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if resource:
        data["resource"] = resource
    return token_request(http, as_meta, client, data)
