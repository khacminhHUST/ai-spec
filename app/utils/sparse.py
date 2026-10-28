"""BM25 sparse vector (tính phía client, Qdrant tính IDF qua modifier=IDF).

Ingest: doc_sparse(text) · Query: query_sparse(text, exact_keywords, boost).
Hai phía PHẢI dùng cùng tokenizer → chỉ dùng module này.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from functools import lru_cache

from app.core.config import load_yaml

K1 = 1.2
B = 0.75
AVG_DOC_LEN = 120.0  # token BM25 trung bình / child (ước lượng; chỉ ảnh hưởng chuẩn hóa độ dài)

_WORD_RE = re.compile(r"\w+", re.UNICODE)


@lru_cache(maxsize=1)
def _keep_re() -> re.Pattern:
    pats = load_yaml("keyword_patterns")["patterns"]
    return re.compile("|".join(f"(?:{p})" for p in pats))


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return text.replace("²", "2").replace("³", "3").lower()


def tokenize(text: str) -> list[str]:
    """Mã kỹ thuật → 1 token (bỏ khoảng trắng bên trong) + các từ thành phần; phần còn lại tách theo từ."""
    tokens: list[str] = []
    pos = 0
    for m in _keep_re().finditer(text):
        tokens += _WORD_RE.findall(_norm(text[pos:m.start()]))
        kept = _norm(re.sub(r"\s+", "", m.group(0)))
        tokens.append(kept)
        parts = _WORD_RE.findall(kept)
        if len(parts) > 1:
            tokens += parts
        pos = m.end()
    tokens += _WORD_RE.findall(_norm(text[pos:]))
    return tokens


def token_id(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "little")


def _to_sparse(weights: dict[int, float]) -> dict[str, list]:
    items = sorted(weights.items())
    return {"indices": [i for i, _ in items], "values": [v for _, v in items]}


def doc_sparse(text: str) -> dict[str, list]:
    """Vector sparse cho child: trọng số TF bão hòa BM25 (IDF do Qdrant)."""
    toks = tokenize(text)
    dl = len(toks) or 1
    norm = K1 * (1 - B + B * dl / AVG_DOC_LEN)
    weights: dict[int, float] = {}
    for tok, tf in Counter(toks).items():
        tid = token_id(tok)
        weights[tid] = weights.get(tid, 0.0) + tf * (K1 + 1) / (tf + norm)
    return _to_sparse(weights)


def query_sparse(text: str, exact_keywords: list[str] | None = None, boost: float = 1.0) -> dict[str, list]:
    """Vector sparse cho query: mỗi token 1.0; token của exact_keywords nhân `boost`."""
    weights = {token_id(t): 1.0 for t in tokenize(text)}
    for t in {t for kw in exact_keywords or [] for t in tokenize(kw)}:
        weights[token_id(t)] = boost
    return _to_sparse(weights)
