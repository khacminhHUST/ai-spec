"""Tách câu giữ nguyên text gốc: "".join(split_sentences(t)) == t.

1. Che (mask) các mẫu dễ nhầm: số thập phân / số mục (1.5, 3,5, 5.7.1), mã tiêu chuẩn (TCVN 5687:2024,
   QCVN 06:2022/BXD), viết tắt (v.v., No., Rev.01) – cấu hình ở config/ingestion.yaml › sentence.
2. Điểm cắt: `. ; ? !` + khoảng trắng + chữ hoa (hoặc mở ngoặc/ngoặc kép + chữ hoa),
   xuống dòng + bullet/số thứ tự, và điểm cắt của underthesea (tiếng Việt) trên text đã che.
3. Cắt trên text gốc tại các vị trí đã chọn (khoảng trắng đi theo câu trước).
"""
from __future__ import annotations

import re
from functools import lru_cache

from ingestion.textutil import ingestion_cfg
from shared.config import load_yaml

_PUNCT = ".;?!…"
_MASK_CHARS = re.compile(r"[.,:;?!/…]")
_BULLET_START = re.compile(r"\n(?=\s*(?:[•▪➢❖✓\-–*+]|\(?\d{1,2}[.)]\s|\(?[a-zA-Z][.)]\s|[ivxIVX]{1,4}[.)]\s))")
_SENT_END = re.compile(r"([.;?!…]+)([\"'”’)\]]*)(\s+)(?=[\"'“‘(\[]?[A-ZÀ-ỸĐ])")


@lru_cache(maxsize=1)
def _protect_re() -> re.Pattern:
    cfg = ingestion_cfg()["sentence"]
    pats = list(cfg.get("protect_patterns", []))
    pats.append(load_yaml("references")["standards"]["pattern"].replace("?P<prefix>", "?:").replace("?P<code>", "?:"))
    pats += load_yaml("keyword_patterns")["patterns"][:1]  # mã tiêu chuẩn
    abbr = sorted(cfg.get("abbreviations", []), key=len, reverse=True)
    pats.append(r"(?<![\w.])(?:" + "|".join(re.escape(a) for a in abbr) + r")")
    return re.compile("|".join(f"(?:{p})" for p in pats))


def mask(text: str) -> str:
    """Text cùng độ dài, dấu câu bên trong mẫu được che thay bằng '_'."""
    chars = list(text)
    for m in _protect_re().finditer(text):
        for i in range(m.start(), m.end()):
            if _MASK_CHARS.match(chars[i]):
                chars[i] = "_"
    return "".join(chars)


def _underthesea_cuts(masked: str) -> set[int]:
    try:
        from underthesea import sent_tokenize
    except Exception:  # noqa: BLE001
        return set()
    cuts, pos = set(), 0
    for s in sent_tokenize(masked):
        s = s.strip()
        if not s:
            continue
        i = masked.find(s, pos)
        if i < 0:
            continue
        end = i + len(s)
        # chỉ nhận điểm cắt sau dấu kết câu chưa bị che
        if masked[end - 1] in _PUNCT:
            j = end
            while j < len(masked) and masked[j].isspace():
                j += 1
            if end < j < len(masked):  # bắt buộc có khoảng trắng sau dấu câu
                cuts.add(j)
        pos = end
    return cuts


def split_sentences(text: str, lang: str = "vi") -> list[str]:
    if not text:
        return []
    masked = mask(text)
    cuts: set[int] = set()
    for m in _SENT_END.finditer(masked):
        cuts.add(m.end())
    for m in _BULLET_START.finditer(masked):
        cuts.add(m.start() + 1)
    if lang == "vi":
        cuts |= _underthesea_cuts(masked)
    out, prev = [], 0
    for c in sorted(cuts):
        if prev < c < len(text):
            out.append(text[prev:c])
            prev = c
    out.append(text[prev:])
    return [s for s in out if s]
