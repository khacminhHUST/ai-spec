"""Verify access token RS256 do user-management ký (D:/ai-planner/MCP_SERVER_INTEGRATION_GUIDE.md mục 4).

- Khóa: theo `kid` trong header. Trùng khóa tĩnh (nhúng / MCP_SERVER_PUBLIC_KEY_PEM) → không gọi mạng; khác → JWKS.
- Tầng kết nối (401): sai chữ ký, hết hạn, sai `iss`, `aud` không chứa resource_url / server_name.
- Tầng tool: quyền đọc trong claim `permissions` – thiếu thì tool trả lỗi (không phải HTTP 403).
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import serialization
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken

from app.services.mcp_server.config import ServerConfig
from app.services.mcp_server.planner_key import PLANNER_PUBLIC_KEY_PEM

log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def key_id(pem: str) -> str:
    """Cách user-management tính kid: 16 ký tự hex đầu SHA-256 của modulus RSA (bytes big-endian)."""
    n = serialization.load_pem_public_key(pem.encode()).public_numbers().n
    return hashlib.sha256(n.to_bytes((n.bit_length() + 7) // 8, "big")).hexdigest()[:16]


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, lifespan=300, timeout=5)


def signing_key(token: str, cfg: ServerConfig):
    static = cfg.public_key_pem or PLANNER_PUBLIC_KEY_PEM
    kid = jwt.get_unverified_header(token).get("kid")
    if kid is None or kid == key_id(static):
        return static
    try:
        return _jwks_client(cfg.jwks()).get_signing_key_from_jwt(token).key
    except Exception as e:  # noqa: BLE001 – mạng / JWKS hỏng / không kid nào khớp
        raise jwt.InvalidKeyError(f"không có khóa cho kid={kid}: {type(e).__name__}") from None


def verify(token: str, cfg: ServerConfig) -> dict:
    """→ claims. Token không dùng được cho server này → jwt.PyJWTError (tầng 401). RS256 ghim cứng."""
    return jwt.decode(token, signing_key(token, cfg), algorithms=["RS256"], issuer=cfg.issuer,
                      audience=cfg.audiences, leeway=cfg.leeway_s,
                      options={"require": ["exp", "iat", "aud", "sub"]})


def actions(claims: dict, cfg: ServerConfig) -> set[str]:
    """Claim permissions[<resource_url | server_name>] → tập quyền. Sai kiểu / thiếu → rỗng (fail-closed)."""
    perms = claims.get("permissions")
    if not isinstance(perms, dict):
        return set()
    for aud in cfg.audiences:
        raw = perms.get(aud)
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = raw.split()
        return {str(a) for a in raw} if isinstance(raw, (list, tuple, set)) else set()
    return set()


def _peek(token: str) -> dict:
    """Claim KHÔNG verify – chỉ để ghi log chẩn đoán."""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except Exception:  # noqa: BLE001
        return {}


class PlannerTokenVerifier:
    """TokenVerifier cho SDK mcp: None → SDK trả 401 kèm WWW-Authenticate (resource_metadata)."""

    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = verify(token, self.cfg)
        except jwt.PyJWTError as e:
            u = _peek(token)
            log.warning("[MCP-SERVER] từ chối token %s | iss=%r aud=%r sub=%r | cần iss=%r aud∈%s",
                        type(e).__name__, u.get("iss"), u.get("aud"), u.get("sub"), self.cfg.issuer, self.cfg.audiences)
            return None
        email = str(claims.get("email") or "").strip().lower()
        sub = str(claims.get("sub") or "").strip()
        if not sub or not email:
            log.warning("[MCP-SERVER] token thiếu sub/email (sub=%r)", sub)
            return None
        return AccessToken(token=token, client_id=sub, subject=sub, scopes=sorted(actions(claims, self.cfg)),
                           expires_at=claims.get("exp"), claims={"email": email})
