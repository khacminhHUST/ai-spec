"""PKCE S256 (RFC 7636) + state ngẫu nhiên. Đối chiếu ai-planner/app/services/oauth/pkce.py (phía A verify)."""
from __future__ import annotations

import base64
import hashlib
import secrets


def new_verifier(nbytes: int = 48) -> str:
    """token_urlsafe(48) → 64 ký tự [A-Za-z0-9_-] (RFC 7636: 43–128)."""
    v = secrets.token_urlsafe(nbytes)
    if not 43 <= len(v) <= 128:
        raise ValueError("code_verifier phải dài 43–128 ký tự")
    return v


def challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)
