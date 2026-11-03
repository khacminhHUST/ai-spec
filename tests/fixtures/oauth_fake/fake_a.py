"""Authorization Server A giả (FastAPI), dựng theo ai-planner/app/api/oauth/router.py + well_known_router.py.

- Discovery RFC 8414, JWKS (1 khóa RSA tự sinh, kid = 16 ký tự đầu SHA-256(modulus)), DCR công khai.
- /oauth/authorize: "trình duyệt" đã đăng nhập qua cookie `fake_a_user` → tự đồng ý; `resource` chưa đăng ký →
  redirect error=invalid_target; chỉ S256.
- /oauth/token: authorization_code (PKCE S256) | refresh_token (xoay vòng; dùng lại token cũ → thu hồi cả phiên
  user+client). Lỗi kiểu FastAPI của A: HTTP 400 {"detail": "invalid_grant"}.
- JWT RS256 payload đúng mục 3.2 hướng dẫn: iss, sub, email, permissions{resource_uri: [...]}, aud[list], jti, iat, exp.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import threading
import uuid
from urllib.parse import urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


def _b64url_uint(v: int) -> str:
    return base64.urlsafe_b64encode(v.to_bytes((v.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


class FakeServerA:
    def __init__(self, issuer: str, servers: dict[str, str], users: dict[str, dict[str, list[str]]], *,
                 access_ttl_s: int = 3600, advertised_expires_in: int | None = None):
        """servers: resource_uri → name (mcp_servers); users: user → {name: [actions]}."""
        self.issuer = issuer
        self.servers = servers
        self.users = users
        self.access_ttl_s = access_ttl_s
        self.advertised_expires_in = advertised_expires_in     # khác access_ttl_s → giả lập token hết hạn sớm
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        n = self.key.public_key().public_numbers().n
        self.kid = hashlib.sha256(n.to_bytes((n.bit_length() + 7) // 8, "big")).hexdigest()[:16]
        self.public_pem = self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}
        self.register_calls = 0
        self.token_calls: list[dict] = []
        self._mu = threading.Lock()
        self.app = self._build()

    # ---------------------------------------------------------------- tiện ích test
    def revoke(self, user: str) -> None:
        for r in self.refresh.values():
            if r["user"] == user:
                r["revoked"] = True

    def refresh_grants(self) -> list[dict]:
        return [c for c in self.token_calls if c.get("grant_type") == "refresh_token"]

    def _permissions(self, user: str, resource: str | None) -> tuple[list[str], dict[str, list[str]]]:
        full = self.users.get(user, {})
        if resource:
            uris = [u for u in resource.split() if u in self.servers]
            return uris, {u: full.get(self.servers[u], []) for u in uris}
        return list(full), full

    def _pair(self, user: str, client_id: str, resource: str | None) -> dict:
        aud, perms = self._permissions(user, resource)
        now = dt.datetime.now(dt.timezone.utc)
        payload = {"iss": self.issuer, "sub": f"uid-{user}", "email": f"{user}@hawee.com.vn", "permissions": perms,
                   "aud": aud, "jti": uuid.uuid4().hex, "iat": now, "exp": now + dt.timedelta(seconds=self.access_ttl_s)}
        access = jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": self.kid})
        raw = secrets.token_urlsafe(32)
        self.refresh[raw] = {"user": user, "client": client_id, "resource": resource, "revoked": False}
        return {"access_token": access, "token_type": "Bearer", "refresh_token": raw,
                "expires_in": self.advertised_expires_in or self.access_ttl_s}

    # ---------------------------------------------------------------- app
    def _build(self) -> FastAPI:
        app = FastAPI()
        a = self

        @app.get("/.well-known/oauth-authorization-server")
        def metadata():
            i = a.issuer
            return {"issuer": i, "authorization_endpoint": f"{i}/oauth/authorize", "token_endpoint": f"{i}/oauth/token",
                    "registration_endpoint": f"{i}/oauth/register", "jwks_uri": f"{i}/.well-known/jwks.json",
                    "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]}

        @app.get("/.well-known/jwks.json")
        def jwks():
            nums = a.key.public_key().public_numbers()
            return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": a.kid, "n": _b64url_uint(nums.n),
                              "e": _b64url_uint(nums.e)}]}

        @app.post("/oauth/register", status_code=201)
        async def register(request: Request):
            body = await request.json()
            if not body.get("redirect_uris"):
                raise HTTPException(400, "redirect_uris must not be empty.")
            for uri in body["redirect_uris"]:
                p = urlsplit(uri)
                if not p.scheme or not p.netloc:
                    raise HTTPException(400, f"Invalid redirect_uri: '{uri}'.")
            a.register_calls += 1
            cid = secrets.token_urlsafe(24)
            a.clients[cid] = {"client_name": body.get("client_name"), "redirect_uris": body["redirect_uris"]}
            return {"client_id": cid, "client_name": body.get("client_name"), "redirect_uris": body["redirect_uris"],
                    "token_endpoint_auth_method": "none"}

        def _redirect(uri: str, params: dict) -> RedirectResponse:
            return RedirectResponse(uri + ("&" if "?" in uri else "?") + urlencode({k: v for k, v in params.items()
                                                                                    if v is not None}))

        @app.get("/oauth/authorize")
        def authorize(request: Request, client_id: str, redirect_uri: str, response_type: str, code_challenge: str,
                      code_challenge_method: str, state: str | None = None, scope: str | None = None,
                      resource: list[str] = Query(default=[])):
            client = a.clients.get(client_id)
            if client is None:
                raise HTTPException(400, "Unknown client_id.")
            if redirect_uri not in client["redirect_uris"]:
                raise HTTPException(400, "redirect_uri is not registered for this client.")
            if response_type != "code":
                return _redirect(redirect_uri, {"error": "unsupported_response_type", "state": state})
            if code_challenge_method != "S256":
                return _redirect(redirect_uri, {"error": "invalid_request", "state": state})
            if any(u not in a.servers for u in resource):
                return _redirect(redirect_uri, {"error": "invalid_target", "state": state})
            user = request.cookies.get("fake_a_user")
            if not user:
                return HTMLResponse("<form>login</form>")            # trang đăng nhập của A
            code = secrets.token_urlsafe(32)
            a.codes[code] = {"user": user, "client": client_id, "challenge": code_challenge, "redirect_uri": redirect_uri,
                             "resource": " ".join(resource) or None, "used": False}
            return _redirect(redirect_uri, {"code": code, "state": state})

        @app.post("/oauth/token")
        def token(grant_type: str = Form(...), code: str | None = Form(None), redirect_uri: str | None = Form(None),
                  client_id: str | None = Form(None), code_verifier: str | None = Form(None),
                  refresh_token: str | None = Form(None), resource: list[str] = Form(default=[])):
            with a._mu:
                a.token_calls.append({"grant_type": grant_type, "client_id": client_id, "resource": resource})
                if grant_type == "authorization_code":
                    row = a.codes.get(code or "")
                    if not row or row["used"] or row["client"] != client_id or row["redirect_uri"] != redirect_uri:
                        return JSONResponse({"detail": "invalid_grant"}, 400)
                    if not hmac.compare_digest(_challenge(code_verifier or ""), row["challenge"]):
                        return JSONResponse({"detail": "invalid_grant"}, 400)
                    row["used"] = True
                    if any(u not in a.servers for u in resource):
                        return JSONResponse({"detail": "invalid_target"}, 400)
                    return a._pair(row["user"], client_id, " ".join(resource) or row["resource"])
                if grant_type == "refresh_token":
                    row = a.refresh.get(refresh_token or "")
                    if row is None:
                        return JSONResponse({"detail": "invalid_grant"}, 400)
                    if row["revoked"]:                    # dùng lại token đã xoay → coi là bị đánh cắp
                        for r in a.refresh.values():
                            if r["user"] == row["user"] and r["client"] == row["client"]:
                                r["revoked"] = True
                        return JSONResponse({"detail": "invalid_grant"}, 400)
                    if client_id is not None and row["client"] != client_id:
                        return JSONResponse({"detail": "invalid_grant"}, 400)
                    row["revoked"] = True
                    return a._pair(row["user"], row["client"], " ".join(resource) or row["resource"])
                return JSONResponse({"detail": "unsupported_grant_type"}, 400)

        return app
