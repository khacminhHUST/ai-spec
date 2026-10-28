"""Đếm token theo ngôn ngữ.

- vi: tokenizer Qwen3-Embedding-8B (HF `tokenizers`, tải 1 lần rồi cache).
- en: Gemini không có tokenizer offline → dùng tokenizer Qwen3 làm xấp xỉ
  (BPE tiếng Anh tương đương; tránh gọi countTokens cho từng chunk).
- Không tải được tokenizer (offline, chưa cache) → ước lượng theo ký tự.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from shared.config import get_settings

log = logging.getLogger(__name__)
_FALLBACK_CHARS_PER_TOKEN = 3.0


@lru_cache(maxsize=4)
def _tokenizer(name: str):
    try:
        from tokenizers import Tokenizer

        return Tokenizer.from_pretrained(name)
    except Exception as e:  # noqa: BLE001
        log.warning("Không tải được tokenizer %s (%s) → ước lượng theo ký tự", name, e)
        return None


def tokenizer_name(lang: str) -> str:
    return get_settings().embedding_cfg("vi")["model"]  # xem docstring: en dùng xấp xỉ


def count_tokens(text: str, lang: str = "vi") -> int:
    tok = _tokenizer(tokenizer_name(lang))
    if tok is None:
        return max(1, round(len(text) / _FALLBACK_CHARS_PER_TOKEN)) if text else 0
    return len(tok.encode(text, add_special_tokens=False).ids)
