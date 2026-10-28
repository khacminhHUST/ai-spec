"""Đếm token (ngưỡng chunk của Ingestion, ngân sách context của Query).

- Tokenizer: config/models.yaml › token_counter.hf_tokenizer (mặc định Qwen3-Embedding-8B).
  Embedding Gemini không có tokenizer offline → dùng BPE Qwen3 làm xấp xỉ cho cả vi/en.
- Nạp từ cache HF cục bộ trước (không gọi mạng); chưa có cache mới tải 1 lần.
- Không có tokenizer (offline, chưa cache) → ước lượng theo ký tự.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import get_settings

log = logging.getLogger(__name__)
_FALLBACK_CHARS_PER_TOKEN = 3.0
DEFAULT_TOKENIZER = "Qwen/Qwen3-Embedding-8B"


@lru_cache(maxsize=4)
def _tokenizer(name: str):
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    for local_only in (True, False):
        try:
            return Tokenizer.from_file(hf_hub_download(name, "tokenizer.json", local_files_only=local_only))
        except Exception as e:  # noqa: BLE001
            err = e
    log.warning("Không tải được tokenizer %s (%s) → ước lượng theo ký tự", name, err)
    return None


def tokenizer_name(lang: str = "vi") -> str:
    return get_settings().models.get("token_counter", {}).get("hf_tokenizer", DEFAULT_TOKENIZER)


def count_tokens(text: str, lang: str = "vi") -> int:
    tok = _tokenizer(tokenizer_name(lang))
    if tok is None:
        return max(1, round(len(text) / _FALLBACK_CHARS_PER_TOKEN)) if text else 0
    return len(tok.encode(text, add_special_tokens=False).ids)
