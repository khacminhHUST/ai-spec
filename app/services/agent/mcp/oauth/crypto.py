"""Mã hóa secret khi lưu DB (Fernet). Khóa lấy từ MEP_SECRET_KEY trong .env.

MEP_SECRET_KEY là chuỗi ngẫu nhiên bất kỳ (khuyến nghị ≥ 32 ký tự) → SHA-256 → khóa Fernet 32 byte.
Đổi MEP_SECRET_KEY = mọi token/header đã lưu không giải mã được (người dùng phải kết nối lại).
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from app.services.agent.mcp.oauth.errors import OAuthError, SecretKeyMissing

ENV_KEY = "MEP_SECRET_KEY"


class SecretBox:
    def __init__(self, secret: str):
        if not secret:
            raise SecretKeyMissing()
        self._f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))

    def encrypt(self, plain: str) -> str:
        return self._f.encrypt(plain.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise OAuthError("decrypt_failed", "Không giải mã được dữ liệu đã lưu (MEP_SECRET_KEY đã đổi?)") from None


def secret_box(secret: str | None = None) -> SecretBox:
    """secret None → đọc MEP_SECRET_KEY (get_settings() đã nạp .env). Thiếu → SecretKeyMissing."""
    if secret is None:
        from app.core.config import get_settings

        get_settings()
        secret = os.environ.get(ENV_KEY, "").strip()
    return SecretBox(secret)
