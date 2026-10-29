"""Tiện ích chung cho tool: kết quả JSON có cấu trúc, cắt chuỗi."""
from __future__ import annotations

import json
from typing import Any


def ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False, default=str)


def fail(error: str, **data: Any) -> str:
    """Lỗi có cấu trúc trả cho agent tự xử lý (không ném exception làm sập vòng lặp)."""
    return json.dumps({"ok": False, "error": error, **data}, ensure_ascii=False, default=str)


def clip(text: str, max_chars: int) -> str:
    """Giữ đầu + cuối (traceback thường ở cuối), đánh dấu phần bị cắt."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n…[cắt {len(text) - 2 * half} ký tự]…\n{text[-half:]}"
