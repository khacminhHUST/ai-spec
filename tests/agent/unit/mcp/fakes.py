"""Fake cho unit test MCP connector: store in-memory (đủ ngữ nghĩa khóa FOR UPDATE), Authorization Server giả
qua httpx.MockTransport (không gọi mạng)."""
from __future__ import annotations

import copy
import datetime as dt
import json
import secrets
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.services.agent.mcp.oauth.pkce import challenge_s256
from app.services.agent.mcp.oauth.store import TOKEN_FIELDS, OAuthStore, utcnow
from app.services.agent.mcp.store import ConnectorStore

SECRET = "unit-test-secret-key-0123456789abcdef"
AS = "https://as.example.com"
MCP_URL = "https://mcp.example.com/mcp"
REDIRECT = "http://localhost:5173/mcp/oauth/callback"


class MemoryStore(OAuthStore, ConnectorStore):
    def __init__(self):
        self.clients: dict[tuple[str, str], dict] = {}
        self.pending: dict[str, dict] = {}
        self.tokens: dict[tuple[str, str], dict] = {}
        self.connectors: dict[str, dict] = {}
        self.overrides: dict[str, dict[str, str]] = defaultdict(dict)
        self._locks: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)
        self._mu = threading.Lock()

    # ---- oauth_clients
    def get_client(self, issuer, redirect_uri):
        return copy.deepcopy(self.clients.get((issuer, redirect_uri)))

    def save_client(self, issuer, redirect_uri, client_id, client_secret_enc, auth_method):
        self.clients[(issuer, redirect_uri)] = {"issuer": issuer, "redirect_uri": redirect_uri, "client_id": client_id,
                                                "client_secret_enc": client_secret_enc, "auth_method": auth_method}

    # ---- oauth_pending
    def add_pending(self, state, user_id, connector_id, code_verifier_enc, expires_at):
        now = utcnow()
        self.pending = {k: v for k, v in self.pending.items() if v["expires_at"] >= now}
        self.pending[state] = {"state": state, "user_id": user_id, "connector_id": connector_id,
                               "code_verifier_enc": code_verifier_enc, "expires_at": expires_at}

    def pop_pending(self, state):
        return self.pending.pop(state, None)

    # ---- mcp_user_tokens
    def get_token(self, user_id, connector_id):
        return copy.deepcopy(self.tokens.get((user_id, connector_id)))

    def save_token(self, user_id, connector_id, **fields):
        assert set(fields) <= set(TOKEN_FIELDS)
        row = self.tokens.setdefault((user_id, connector_id), {"user_id": user_id, "connector_id": connector_id,
                                                               "status": "connected"})
        row.update(fields)

    def delete_token(self, user_id, connector_id):
        self.tokens.pop((user_id, connector_id), None)

    def delete_connector_tokens(self, connector_id):
        self.tokens = {k: v for k, v in self.tokens.items() if k[1] != connector_id}

    def token_statuses(self, user_id):
        return {c: r["status"] for (u, c), r in self.tokens.items() if u == user_id}

    @contextmanager
    def lock_token(self, user_id, connector_id):
        with self._mu:
            lock = self._locks[(user_id, connector_id)]
        with lock:
            row = self.get_token(user_id, connector_id)

            def update(**fields):
                assert set(fields) <= set(TOKEN_FIELDS)
                self.tokens[(user_id, connector_id)].update(fields)

            yield row, update

    # ---- mcp_connectors / overrides
    def list_connectors(self, owner):
        return [copy.deepcopy(c) for c in sorted(self.connectors.values(), key=lambda c: c["name"])
                if c.get("created_by") == owner]

    def get_connector(self, connector_id):
        return copy.deepcopy(self.connectors.get(connector_id))

    def insert_connector(self, row):
        if any(c["name"] == row["name"] and c.get("created_by") == row.get("created_by") for c in self.connectors.values()):
            raise ValueError("trùng tên")
        self.connectors[row["connector_id"]] = {"enabled": True, **copy.deepcopy(row)}

    def update_connector(self, connector_id, **fields):
        self.connectors[connector_id].update(copy.deepcopy(fields))

    def delete_connector(self, connector_id):
        self.connectors.pop(connector_id, None)
        self.overrides.pop(connector_id, None)
        self.tokens = {k: v for k, v in self.tokens.items() if k[1] != connector_id}
        self.pending = {k: v for k, v in self.pending.items() if v["connector_id"] != connector_id}

    def get_overrides(self, connector_id):
        return dict(self.overrides.get(connector_id, {}))

    def set_override(self, connector_id, tool_name, access):
        if access is None:
            self.overrides[connector_id].pop(tool_name, None)
        else:
            self.overrides[connector_id][tool_name] = access


# ------------------------------------------------------------------ Authorization Server giả (MockTransport)

class FakeAS:
    """AS kiểu Server A: discovery, DCR công khai, token (S256, xoay vòng refresh, dùng lại → thu hồi cả phiên).

    Lỗi trả kiểu FastAPI của A: HTTP 400 {"detail": "invalid_grant"}.
    """

    def __init__(self, issuer: str = AS, *, registration: bool = True, expires_in: int = 3600,
                 token_delay_s: float = 0.0):
        self.issuer = issuer
        self.registration = registration
        self.expires_in = expires_in
        self.token_delay_s = token_delay_s
        self.register_calls = 0
        self.token_calls: list[dict[str, str]] = []
        self.codes: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}          # token → {user, client, revoked}
        self._n = 0
        self._mu = threading.Lock()

    def metadata(self) -> dict[str, Any]:
        m = {"issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/oauth/authorize",
             "token_endpoint": f"{self.issuer}/oauth/token", "jwks_uri": f"{self.issuer}/.well-known/jwks.json",
             "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
             "grant_types_supported": ["authorization_code", "refresh_token"],
             "token_endpoint_auth_methods_supported": ["none"]}
        if self.registration:
            m["registration_endpoint"] = f"{self.issuer}/oauth/register"
        return m

    def issue_code(self, user: str, client_id: str, redirect_uri: str, challenge: str) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"user": user, "client": client_id, "redirect_uri": redirect_uri, "challenge": challenge}
        return code

    def _pair(self, user: str, client: str) -> dict[str, Any]:
        self._n += 1
        rt = f"rt-{self._n}-{secrets.token_hex(4)}"
        self.refresh[rt] = {"user": user, "client": client, "revoked": False}
        return {"access_token": f"at-{user}-{self._n}-{secrets.token_hex(4)}", "token_type": "Bearer",
                "expires_in": self.expires_in, "refresh_token": rt}

    def _err(self, detail: str) -> httpx.Response:
        return httpx.Response(400, json={"detail": detail})

    def token(self, form: dict[str, str]) -> httpx.Response:
        if self.token_delay_s:
            time.sleep(self.token_delay_s)
        with self._mu:
            self.token_calls.append(form)
            if form.get("grant_type") == "authorization_code":
                row = self.codes.pop(form.get("code", ""), None)
                if not row or row["client"] != form.get("client_id") or row["redirect_uri"] != form.get("redirect_uri"):
                    return self._err("invalid_grant")
                if challenge_s256(form.get("code_verifier", "")) != row["challenge"]:
                    return self._err("invalid_grant")
                return httpx.Response(200, json=self._pair(row["user"], row["client"]))
            if form.get("grant_type") == "refresh_token":
                row = self.refresh.get(form.get("refresh_token", ""))
                if row is None:
                    return self._err("invalid_grant")
                if row["revoked"]:                       # dùng lại token đã xoay → thu hồi cả phiên
                    self.revoke(row["user"], row["client"])
                    return self._err("invalid_grant")
                row["revoked"] = True
                return httpx.Response(200, json=self._pair(row["user"], row["client"]))
            return self._err("unsupported_grant_type")

    def revoke(self, user: str, client: str | None = None) -> None:
        for r in self.refresh.values():
            if r["user"] == user and (client is None or r["client"] == client):
                r["revoked"] = True

    def handle(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if not str(request.url).startswith(self.issuer):
            return None
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=self.metadata())
        if path == "/oauth/register" and self.registration:
            self.register_calls += 1
            body = json.loads(request.content)
            return httpx.Response(201, json={"client_id": f"client-{self.register_calls}",
                                             "client_name": body["client_name"],
                                             "redirect_uris": body["redirect_uris"],
                                             "token_endpoint_auth_method": "none"})
        if path == "/oauth/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            return self.token(form)
        return httpx.Response(404, text="Not Found")


def mock_http(*handlers) -> httpx.Client:
    """httpx.Client với MockTransport: thử lần lượt handler (trả None = không phải của tôi) → 404."""
    def route(request: httpx.Request) -> httpx.Response:
        for h in handlers:
            resp = h(request)
            if resp is not None:
                return resp
        return httpx.Response(404, text="Not Found")

    return httpx.Client(transport=httpx.MockTransport(route))


def prm_server(mcp_url: str = MCP_URL, issuer: str = AS, *, header: bool = True, suffix: bool = True,
               root: bool = True, as_list: list[str] | None = None):
    """MCP server giả: POST /mcp → 401 (+ resource_metadata nếu header), PRM ở route đuôi /mcp và/hoặc route gốc."""
    from urllib.parse import urlparse

    u = urlparse(mcp_url)
    origin = f"{u.scheme}://{u.netloc}"
    prm = {"resource": mcp_url, "authorization_servers": as_list or [issuer], "bearer_methods_supported": ["header"]}

    def handle(request: httpx.Request):
        if not str(request.url).startswith(origin):
            return None
        p = request.url.path
        if request.method == "POST" and p == u.path:
            h = {"WWW-Authenticate": f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource{u.path}"'}
            return httpx.Response(401, headers=h if header else {}, json={"error": "invalid_token"})
        if p == f"/.well-known/oauth-protected-resource{u.path}" and suffix:
            return httpx.Response(200, json=prm)
        if p == "/.well-known/oauth-protected-resource" and root:
            return httpx.Response(200, json=prm)
        return None

    return handle


def connector_row(**kw) -> dict[str, Any]:
    fa = FakeAS()
    row = {"connector_id": "c1", "name": "kho", "display_name": "Kho", "url": MCP_URL, "transport": "streamable_http",
           "auth_type": "oauth", "headers_enc": None, "resource": MCP_URL, "issuer": AS, "as_metadata": fa.metadata(),
           "scopes": None, "enabled": True, "created_by": "u1"}
    row.update(kw)
    return row


def expired(seconds: int = 10) -> dt.datetime:
    return utcnow() + dt.timedelta(seconds=seconds)
