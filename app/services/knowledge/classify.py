"""Nhận diện loại tài liệu (doc_type) và phân hệ (disciplines) cho upload gọn (docs/knowledge-admin.md mục 2.2).

Worker gọi trước ingestion khi job để trống 2 trường này: đọc tên file + 3 trang đầu (kết quả parse PDF đã có cache
theo file_hash nên ingestion không parse lại), hỏi LLM (config/models.yaml › chunking_llm), rồi truyền kết quả vào
ingestion như metadata ghi đè. LLM lỗi / trả giá trị lạ → dự phòng: loại theo luồng, phân hệ GENERAL (luôn tìm thấy
được – app/repositories/qdrant.py › discipline_filter luôn gồm GENERAL). Người duyệt sửa được ở bước nháp.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models.document import Discipline
from app.prompts import render
from app.services.knowledge.config import DOC_TYPES

log = logging.getLogger(__name__)

# Luồng → loại tài liệu mặc định (DESIGN_CONSULTANT không xác định được thì dùng DESIGN_REPORT)
STREAM_DOC_TYPE = {
    "LEGAL_STANDARD": "LEGAL_STANDARD",
    "OPERATOR_STANDARD": "BRAND_STANDARD",
    "CONTRACT": "CONTRACT",
    "DESIGN_CONSULTANT": "DESIGN_REPORT",
}
MAX_CHARS = 6000
MAX_PAGES = 3


def fallback(stream: str | None) -> dict[str, Any]:
    return {"doc_type": STREAM_DOC_TYPE.get(stream or "", "DESIGN_REPORT"), "disciplines": ["GENERAL"],
            "source": "fallback"}


def first_pages_text(blocks: list[Any], max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS) -> str:
    """Text các trang đầu (bảng: nối các ô) – đủ cho bìa + mục lục."""
    parts: list[str] = []
    for b in blocks:
        if getattr(b, "page", 99) > max_pages:
            continue
        if getattr(b, "kind", "") == "table":
            parts.append(" | ".join(" ".join(r) for r in (getattr(b, "rows", None) or [])))
        else:
            parts.append(getattr(b, "text", "") or "")
    return "\n".join(p for p in parts if p.strip())[:max_chars]


def parse_answer(text: str, stream: str | None, doc_types: list[str] | None = None) -> dict[str, Any]:
    """JSON trả về của LLM → {doc_type, disciplines, source}; trường lạ / thiếu → lấy phần dự phòng."""
    doc_types = doc_types or list(DOC_TYPES)
    fb = fallback(stream)
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        raw = json.loads(m.group(0)) if m else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    dt = str(raw.get("doc_type") or "").strip().upper()
    valid = {d.value for d in Discipline}
    disc = [d for d in dict.fromkeys(str(x).strip().upper() for x in raw.get("disciplines") or []) if d in valid]
    ok_dt, ok_disc = dt in doc_types, bool(disc)
    return {"doc_type": dt if ok_dt else fb["doc_type"], "disciplines": disc or fb["disciplines"],
            "source": "llm" if ok_dt and ok_disc else ("llm_partial" if ok_dt or ok_disc else "fallback")}


def classify(blocks: list[Any], file_name: str, stream: str | None, llm=None) -> dict[str, Any]:
    """→ {doc_type, disciplines, source: llm | llm_partial | fallback}. Không bao giờ ném lỗi."""
    from langchain_core.messages import HumanMessage, SystemMessage

    text = first_pages_text(blocks)
    if not text.strip():
        return fallback(stream)
    system = render("knowledge/classify_system",
                    doc_types="\n".join(f"- {k}: {v}" for k, v in DOC_TYPES.items()),
                    disciplines=", ".join(d.value for d in Discipline))
    human = render("knowledge/classify_human", file_name=file_name, stream=stream or "?", text=text)
    try:
        if llm is None:
            from app.providers.llm import get_chat_model

            llm = get_chat_model("chunking")
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        content = resp.content if isinstance(resp.content, str) else "".join(   # Gemini: danh sách khối nội dung
            p.get("text", "") if isinstance(p, dict) else str(p) for p in resp.content)
        return parse_answer(content, stream)
    except Exception as e:  # noqa: BLE001 – nhận diện là bước phụ, không được làm hỏng job
        log.warning("classify lỗi (%s) → dùng dự phòng theo luồng", e)
        return fallback(stream)
