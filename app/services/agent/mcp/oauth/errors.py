"""Lỗi OAuth có mã (code) để API/agent báo rõ cho người dùng. Thông điệp KHÔNG bao giờ chứa token."""
from __future__ import annotations


class OAuthError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class ReauthRequired(OAuthError):
    """Token hết hạn/bị thu hồi, refresh không được → người dùng cần bấm Kết nối lại."""

    def __init__(self, message: str = "Kết nối đã hết hạn – cần kết nối lại"):
        super().__init__("needs_reauth", message)


class SecretKeyMissing(OAuthError):
    def __init__(self):
        super().__init__("secret_key_missing",
                         "Thiếu MEP_SECRET_KEY trong .env – không lưu/đọc được token, header bí mật")
