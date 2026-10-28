"""Tiện ích text dùng chung trong ingestion: chuẩn hóa ký tự, nhận diện ngôn ngữ, config."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

from shared.config import load_yaml

# Ký tự font Symbol/Wingdings (vùng Private Use) → ký tự chuẩn. U+FFFE: gạch nối mềm khi xuống dòng.
_CHAR_MAP = {
    "": "•", "": "▪", "": "➢", "": "❖", "": "✓", "": "-", "": "→",
    "￾": "-", "­": "", " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
    "\r": "",
}
_CHAR_RE = re.compile("|".join(map(re.escape, _CHAR_MAP)))
_WS_RE = re.compile(r"[ \t]+")


def normalize_text(text: str) -> str:
    """NFC + map ký tự đặc biệt + gộp khoảng trắng (giữ xuống dòng)."""
    text = unicodedata.normalize("NFC", text or "")
    text = _CHAR_RE.sub(lambda m: _CHAR_MAP[m.group(0)], text)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def squash(text: str) -> str:
    """So sánh toàn vẹn: bỏ toàn bộ khoảng trắng + ký hiệu markdown/¶."""
    return re.sub(r"[\s|¶]+", "", text)


@lru_cache(maxsize=1)
def ingestion_cfg() -> dict[str, Any]:
    return load_yaml("ingestion")


def chunk_cfg() -> dict[str, int]:
    return ingestion_cfg()["chunking"]


@lru_cache(maxsize=1)
def headings_cfg() -> dict[str, Any]:
    return load_yaml("headings")


def profile(doc_type: str | None) -> dict[str, Any]:
    cfg = headings_cfg()
    prof = dict(cfg.get("defaults", {}))
    prof.update(cfg["profiles"].get(doc_type or "", cfg["profiles"]["LEGAL_STANDARD"]))
    return prof


# ---------------------------------------------------------------- ngôn ngữ

_VI_CHARS = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")


@lru_cache(maxsize=1)
def _detector():
    from lingua import Language, LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_languages(Language.VIETNAMESE, Language.ENGLISH).build()


def detect_lang(text: str) -> str | None:
    """'vi' | 'en' | None (quá ngắn / không có chữ). Dấu tiếng Việt → vi ngay; còn lại hỏi lingua."""
    letters = [c for c in text.lower() if c.isalpha()]
    if len(letters) < ingestion_cfg()["language"]["min_chars_detect"]:
        vi = sum(c in _VI_CHARS for c in letters)
        return "vi" if vi >= 2 else None
    vi = sum(c in _VI_CHARS for c in letters)
    if vi / len(letters) > 0.04:
        return "vi"
    from lingua import Language

    lang = _detector().detect_language_of(text)
    if lang is None:
        return None
    return "vi" if lang == Language.VIETNAMESE else "en"


def has_letters(text: str) -> bool:
    return any(c.isalpha() for c in text)
