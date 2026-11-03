"""Khám phá MCP server (docs/mcp-connectors.md mục 2.1).

1. POST <url> `initialize` không token: 2xx → gợi ý `none`; 401 → đọc `resource_metadata` trong WWW-Authenticate.
2. Protected Resource Metadata (RFC 9728): URL trong header → /.well-known/oauth-protected-resource/<path> → bản gốc.
3. Authorization Server Metadata (RFC 8414 / OIDC), kiểm tra `issuer` khớp giá trị trong PRM.
4. Không có PRM (MCP 2025-03-26): gốc của URL MCP là Authorization Server.

So khớp issuer theo `mcp.client.auth.utils.issuers_match`: so chuỗi tuyệt đối, chỉ coi `https://host` và `https://host/`
là một (RFC 3986 §6.2.3 – SDK `mcp` chính thức luôn ghi PRM gốc kèm `/`). Thừa `/` ở issuer có path, sai scheme,
thêm cổng → từ chối.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    issuers_match,
)
from mcp.types import LATEST_PROTOCOL_VERSION

from app.services.agent.mcp.oauth.errors import OAuthError

log = logging.getLogger(__name__)

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION}


@dataclass
class Discovery:
    auth_type: str                              # "oauth" | "none"
    status_code: int
    resource: str | None = None                 # URI chính tắc gửi kèm `resource=` (RFC 8707)
    issuer: str | None = None
    as_metadata: dict[str, Any] | None = None
    scopes: list[str] | None = None
    has_prm: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def registration(self) -> bool:
        return bool((self.as_metadata or {}).get("registration_endpoint"))


def check_url(url: str, allow_http_localhost: bool = True) -> str:
    """Chỉ https://, trừ http://localhost | 127.0.0.1 khi dev. → url đã strip."""
    url = (url or "").strip()
    u = urlparse(url)
    if u.scheme == "https" and u.netloc:
        return url
    if u.scheme == "http" and allow_http_localhost and u.hostname in ("localhost", "127.0.0.1"):
        return url
    raise OAuthError("invalid_url", "URL phải là https:// (http chỉ cho localhost/127.0.0.1 khi dev)")


def _initialize_body(client_name: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": client_name, "version": "1.0"}}}


def _json(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        obj = json.loads(resp.content)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def fetch_prm(http: httpx.Client, url: str, www_auth_url: str | None) -> dict[str, Any] | None:
    for u in build_protected_resource_metadata_discovery_urls(www_auth_url, url):
        try:
            r = http.get(u, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            log.info("PRM %s lỗi: %s", u, type(e).__name__)
            continue
        obj = _json(r) if r.status_code == 200 else None
        if obj and obj.get("authorization_servers"):
            return obj
    return None


def fetch_as_metadata(http: httpx.Client, issuer: str, mcp_url: str, *, legacy: bool) -> dict[str, Any]:
    """AS metadata có `issuer` khớp. Không tìm thấy / issuer lệch → OAuthError."""
    urls = build_oauth_authorization_server_metadata_discovery_urls(None if legacy else issuer, mcp_url)
    if legacy:
        urls.append(issuer + "/.well-known/openid-configuration")
    for u in urls:
        try:
            r = http.get(u, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise OAuthError("discovery_failed", f"Không gọi được {u}: {type(e).__name__}") from None
        if r.status_code >= 500:
            raise OAuthError("discovery_failed", f"{u} trả HTTP {r.status_code}")
        obj = _json(r) if r.status_code == 200 else None
        if not obj:
            continue
        got = str(obj.get("issuer") or "")
        if not issuers_match(got, issuer):
            raise OAuthError("issuer_mismatch", f"issuer của Authorization Server lệch: '{got}' ≠ '{issuer}'")
        for key in ("authorization_endpoint", "token_endpoint"):
            if not obj.get(key):
                raise OAuthError("discovery_failed", f"metadata thiếu {key}")
        methods = obj.get("code_challenge_methods_supported")
        if methods is not None and "S256" not in methods:
            raise OAuthError("discovery_failed", "Authorization Server không hỗ trợ PKCE S256")
        return obj
    raise OAuthError("discovery_failed", f"Không tìm thấy metadata của Authorization Server {issuer}")


def discover(url: str, http: httpx.Client, *, client_name: str = "MEP Project Assistant") -> Discovery:
    try:
        r = http.post(url, json=_initialize_body(client_name), headers=MCP_HEADERS)
    except httpx.HTTPError as e:
        raise OAuthError("unreachable", f"Không kết nối được {url}: {type(e).__name__}") from None
    if 200 <= r.status_code < 300:
        return Discovery("none", r.status_code, notes=["Server trả lời không cần token – gợi ý: Không xác thực"])
    if r.status_code != 401:
        raise OAuthError("discovery_failed", f"Server trả HTTP {r.status_code} cho initialize (cần 2xx hoặc 401)")
    prm = fetch_prm(http, url, extract_resource_metadata_from_www_auth(r))
    if prm:
        issuer = str(prm["authorization_servers"][0])
        as_meta = fetch_as_metadata(http, issuer, url, legacy=False)
        scopes = prm.get("scopes_supported")
        return Discovery("oauth", r.status_code, resource=str(prm.get("resource") or url), issuer=as_meta["issuer"],
                         as_metadata=as_meta, scopes=list(scopes) if scopes else None, has_prm=True)
    u = urlparse(url)
    origin = f"{u.scheme}://{u.netloc}"
    as_meta = fetch_as_metadata(http, origin, url, legacy=True)
    return Discovery("oauth", r.status_code, resource=url, issuer=as_meta["issuer"], as_metadata=as_meta,
                     notes=["Không có Protected Resource Metadata – dùng gốc URL làm Authorization Server"])
