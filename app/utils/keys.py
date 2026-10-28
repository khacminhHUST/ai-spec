"""Quy ước ID dùng chung Ingestion ↔ Query.

section_key (giống nhau giữa VN và EN):
    section_key("5.7.1")              -> "sec_5_7_1"
    section_key("4.3", "table")       -> "table_4_3"
    section_key("15C.6")              -> "sec_15c_6"
    section_key("GR1")                -> "sec_gr1"
    section_key("2", "chap")          -> "chap_2"
    section_key("3", scope="chap_2")  -> "chap_2__sec_3"   # số mục đánh lại theo chương (Chỉ dẫn PTN)
    section_key("5", "art")           -> "art_5"           # Điều 5 (Hợp đồng)
    section_key("A", "appx")          -> "appx_a"          # Phụ lục A

parent_id = "<doc_id>:<lang>:<section_key>"; child_id = "<parent_id>#<seq:04d>".

referenced_ids (dạng chuẩn hóa):
    tiêu chuẩn ngoài: "TCVN 5687:2024", "QCVN 06:2022/BXD", "ASHRAE 62.1", "IEC 60331-21"
    tham chiếu nội bộ: "table_4_3" (Bảng/Table 4.3), "sec_15c" (Section 15C), "chap_2", "art_5", "appx_a"
Regex tham chiếu: config/references.yaml.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from app.core.config import load_yaml

SECTION_KINDS = ("sec", "table", "chap", "part", "art", "appx")


def normalize_number(number: str) -> str:
    """'5.7.1' -> '5_7_1', '15C.6' -> '15c_6', 'GR1' -> 'gr1', 'IV' -> 'iv'."""
    s = strip_accents(number).lower().strip().rstrip(".")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s:
        raise ValueError(f"số mục rỗng: {number!r}")
    return s


def section_key(number: str, kind: str = "sec", scope: str | None = None) -> str:
    if kind not in SECTION_KINDS:
        raise ValueError(f"kind không hợp lệ: {kind}")
    key = f"{kind}_{normalize_number(number)}"
    return f"{scope}__{key}" if scope else key


def make_parent_id(doc_id: str, lang: str, section_key: str) -> str:
    return f"{doc_id}:{lang}:{section_key}"


def make_child_id(parent_id: str, seq: int) -> str:
    return f"{parent_id}#{seq:04d}"


def child_seq(child_id: str) -> int:
    return int(child_id.rsplit("#", 1)[1])


# ---------------------------------------------------------------- alias

def strip_accents(text: str) -> str:
    text = text.replace("Đ", "D").replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize_alias(text: str) -> str:
    """Lowercase, bỏ dấu, bỏ khoảng trắng/ký tự đặc biệt: 'Bãi Đất Đỏ' -> 'baidatdo'."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(text).lower())


# ---------------------------------------------------------------- tham chiếu

@lru_cache(maxsize=1)
def _ref_patterns() -> tuple[re.Pattern, list[tuple[str, re.Pattern]]]:
    cfg = load_yaml("references")
    std = re.compile(cfg["standards"]["pattern"])
    internal = [(r["kind"], re.compile(r["pattern"], re.IGNORECASE)) for r in cfg["internal"]]
    return std, internal


def normalize_standard(prefix: str, code: str) -> str:
    prefix = re.sub(r"\s+", " ", prefix.strip().upper())
    code = re.sub(r"\s+", "", code.strip())
    return f"{prefix} {code}"


def extract_references(text: str) -> list[str]:
    """Bắt mã tham chiếu trong text → danh sách ID chuẩn hóa (không trùng, giữ thứ tự)."""
    std, internal = _ref_patterns()
    out: list[str] = []
    for m in std.finditer(text):
        out.append(normalize_standard(m.group("prefix"), m.group("code")))
    for kind, pat in internal:
        for m in pat.finditer(text):
            out.append(section_key(m.group("num"), kind))
    return list(dict.fromkeys(out))


def is_internal_ref(ref_id: str) -> bool:
    return ref_id.split("_", 1)[0] in SECTION_KINDS and " " not in ref_id
