"""Danh tính người dùng khi app chạy sau web Tri thức (deploy/trithuc/README.md).

Bật bằng biến môi trường MEP_AUTH_MODE=trusted_proxy. Chuỗi tin cậy:
  trình duyệt gửi `Authorization: Bearer <token web Tri thức>`
  → nginx_proxy (server 1) kiểm token bằng auth_request → hawee-backend /api/auth/phien (get_current_user:
    chỉ nhận access token, đọc DB, tài khoản đã xóa bị từ chối) → chỉ chuyển request hợp lệ sang server 2
  → gateway server 2 chỉ nhận kết nối từ IP server 1.
Vì vậy ở đây chỉ ĐỌC payload JWT (không kiểm chữ ký, không cần JWT_SECRET) để lấy email, rồi:
  - ghi đè `user_id` trong query string và trong body JSON bằng email (bỏ giá trị trình duyệt tự gửi);
  - GET /conversations/{id}: hội thoại của người khác → 403 (route gốc không kiểm chủ sở hữu).
Giới hạn: body multipart không sửa (đọc lại file 200 MB quá tốn) – `user_id` dạng Form ở /knowledge/uploads chỉ dùng
để ghi vết uploaded_by; frontend gửi đúng email lấy từ token.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode

import anyio

EXEMPT_PATHS = frozenset({"/healthz", "/mcp/oauth/callback"})  # callback OAuth: trình duyệt bị chuyển hướng về, không có Bearer
# MCP server cho Claude (app/services/mcp_server): token user-management, MCP server tự verify chữ ký – không qua đây
EXEMPT_PREFIXES = ("/kb-mcp", "/.well-known/oauth-protected-resource/")
MAX_JSON_BYTES = 2 * 1024 * 1024
_CONV_RE = re.compile(r"^/conversations/([^/]+)$")


def jwt_payload(token: str) -> dict[str, Any] | None:
    """Payload JWT (không kiểm chữ ký). Sai định dạng → None."""
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def identity(token: str | None, claim: str | None = None, now: float | None = None) -> str | None:
    """Email trong access token còn hạn; refresh token / hết hạn / thiếu trường → None."""
    p = jwt_payload(token) if token else None
    if not p:
        return None
    if p.get("type") not in (None, "access"):
        return None
    exp = p.get("exp")
    if isinstance(exp, (int, float)) and exp < (now if now is not None else time.time()):
        return None
    for k in [claim] if claim else ["email", "sub"]:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _header(scope: dict, name: bytes) -> str:
    return next((v.decode("latin-1") for k, v in scope["headers"] if k == name), "")


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class TrustedProxyAuth:
    """Middleware ASGI thuần (không đệm response → SSE vẫn stream)."""

    def __init__(self, app, user_claim: str | None = None):
        self.app = app
        self.claim = user_claim

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in EXEMPT_PATHS or scope["path"].startswith(EXEMPT_PREFIXES):
            return await self.app(scope, receive, send)
        auth = _header(scope, b"authorization")
        email = identity(auth[7:].strip() if auth.lower().startswith("bearer ") else None, self.claim)
        if not email:
            return await _send_json(send, 401, "Yêu cầu đăng nhập web Tri thức")

        scope = dict(scope)
        scope.setdefault("state", {})["user_email"] = email
        qs = scope.get("query_string", b"").decode("latin-1")
        if qs:
            pairs = [(k, email if k == "user_id" else v) for k, v in parse_qsl(qs, keep_blank_values=True)]
            scope["query_string"] = urlencode(pairs).encode("latin-1")

        m = _CONV_RE.match(scope["path"])
        if m and scope["method"] == "GET":
            deps = scope["app"].state.deps
            conv = await anyio.to_thread.run_sync(deps.repo.get_conversation, m.group(1))
            if conv and conv.get("user_id") != email:
                return await _send_json(send, 403, "conversation thuộc user khác")

        if scope["method"] in ("POST", "PUT", "PATCH") and _header(scope, b"content-type").startswith("application/json"):
            chunks, size = [], 0
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    return
                chunks.append(msg.get("body", b""))
                size += len(chunks[-1])
                if size > MAX_JSON_BYTES:
                    return await _send_json(send, 413, "body JSON quá lớn")
                if not msg.get("more_body"):
                    break
            body = b"".join(chunks)
            try:
                obj = json.loads(body) if body else None
            except ValueError:
                obj = None
            if isinstance(obj, dict) and "user_id" in obj:
                obj["user_id"] = email
                body = json.dumps(obj, ensure_ascii=False).encode()
                scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"content-length"]
                scope["headers"].append((b"content-length", str(len(body)).encode()))
            replayed = False

            async def receive_replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await self.app(scope, receive_replay, send)
        return await self.app(scope, receive, send)
