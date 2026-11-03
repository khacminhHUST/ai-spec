"""MCP server giả (SDK chính thức `mcp.server.fastmcp`) cho integration test connector.

- `oauth_server`: TokenVerifier theo mục 5.5 hướng dẫn (RS256 pin cứng, iss/aud, permissions → scope) – 401 kèm
  WWW-Authenticate resource_metadata, 403 insufficient_scope (scope "cửa vào" ACCESS), ToolError theo action.
  PRM có cả route đuôi /mcp (SDK tự mount) lẫn route gốc (custom_route). Tool: list_items, whoami (readOnlyHint),
  create_item (destructiveHint, cần write), purge (cần delete).
- `open_server`: không xác thực (echo readOnlyHint, ping không annotation → write).
- `headers_server`: như open_server nhưng bắt buộc header `X-API-Key` (sai/thiếu → 401).
"""
from __future__ import annotations

import json
from typing import Any

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

SERVER_NAME = "ai-kho"
ACCESS = f"{SERVER_NAME}:access"
ACTIONS = {"read", "write", "delete"}
RO = ToolAnnotations(readOnlyHint=True)


class PlannerTokenVerifier:
    """Trả None → SDK 401; AccessToken thiếu ACCESS → SDK 403 insufficient_scope."""

    def __init__(self, public_pem: str, issuer: str, resource: str):
        self.public_pem, self.issuer, self.resource = public_pem, issuer, resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(token, self.public_pem, algorithms=["RS256"], issuer=self.issuer,
                                audience=[self.resource, SERVER_NAME], options={"require": ["exp", "iat", "aud", "sub"]})
        except jwt.PyJWTError:
            return None
        perms = claims.get("permissions") if isinstance(claims.get("permissions"), dict) else {}
        raw = perms.get(self.resource, perms.get(SERVER_NAME, []))
        actions = {x for x in (raw.split() if isinstance(raw, str) else raw or []) if x in ACTIONS}
        scopes = [ACCESS, *(f"{SERVER_NAME}:{x}" for x in sorted(actions))] if actions else []
        return AccessToken(token=token, client_id=claims["sub"], subject=claims["sub"], scopes=scopes,
                           claims={"email": claims.get("email", "")})


def _require(action: str) -> str:
    tok = get_access_token()
    granted = {s.split(":", 1)[1] for s in (tok.scopes if tok else []) if s.startswith(SERVER_NAME + ":")}
    if action not in granted:
        raise ToolError(f"Không có quyền '{action}'. Quyền hiện có: {sorted(granted - {'access'}) or 'không có'}.")
    return (tok.claims or {}).get("email", "")


def oauth_server(issuer: str, resource: str, public_pem: str, created: list) -> FastMCP:
    mcp = FastMCP(SERVER_NAME, token_verifier=PlannerTokenVerifier(public_pem, issuer, resource),
                  auth=AuthSettings(issuer_url=issuer, resource_server_url=resource, required_scopes=[ACCESS],
                                    validate_token_resource=False),
                  streamable_http_path="/mcp")

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    async def prm_root(request: Request):
        return JSONResponse({"resource": resource, "authorization_servers": [issuer],
                             "bearer_methods_supported": ["header"], "scopes_supported": [ACCESS]})

    @mcp.tool(annotations=RO)
    def list_items() -> dict[str, Any]:
        """Liệt kê vật tư trong kho."""
        _require("read")
        return {"items": [{"ma": "VT01", "ten": "Ống thép DN50", "ton": 120}]}

    @mcp.tool(annotations=RO)
    def whoami() -> dict[str, Any]:
        """Email người dùng của token hiện tại."""
        return {"email": _require("read")}

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    def create_item(ten: str) -> dict[str, Any]:
        """Tạo vật tư mới trong kho."""
        email = _require("write")
        created.append((email, ten))
        return {"created": True, "ten": ten}

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    def purge() -> dict[str, Any]:
        """Xóa toàn bộ vật tư (cần quyền delete)."""
        _require("delete")
        return {"purged": True}

    return mcp


def open_server(name: str = "pub") -> FastMCP:
    mcp = FastMCP(name, streamable_http_path="/mcp")

    @mcp.tool(annotations=RO)
    def echo(text: str) -> str:
        """Trả lại nguyên văn."""
        return f"echo: {text}"

    @mcp.tool()
    def ping() -> str:
        """Ping (không annotation → coi là write)."""
        return "pong"

    return mcp


def require_header(app, header: str, value: str):
    """Bọc ASGI app: thiếu/sai header → 401 (không WWW-Authenticate)."""
    key = header.lower().encode()

    async def wrapped(scope, receive, send):
        if scope["type"] == "http" and dict(scope.get("headers") or []).get(key, b"").decode() != value:
            body = json.dumps({"error": "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return wrapped
