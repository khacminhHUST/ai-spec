"""Cấu hình kho tri thức: config/knowledge.yaml (+ danh mục stream / discipline / doc_type / stage cho form)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import ROOT, load_yaml
from app.models.document import Discipline, Stream

DEFAULTS: dict[str, Any] = {
    "access": {"mode": "open"},
    "uploads_dir": "data/uploads",
    "artifacts_dir": "artifacts/jobs",
    "max_upload_mb": 200,
    "worker": {"poll_interval_s": 3},
}

# Mô tả ngắn cho form upload (doc_type = profile trong config/headings.yaml)
DOC_TYPES: dict[str, str] = {
    "SPEC": "Yêu cầu kỹ thuật (Spec) của tư vấn – có thể song ngữ EN | VN theo cột",
    "TECH_INSTRUCTION": "Chỉ dẫn kỹ thuật – CHƯƠNG N › N. › N.N",
    "DESIGN_REPORT": "Thuyết minh thiết kế – I. › 1. › 5.1 › a.",
    "BRAND_STANDARD": "Tiêu chuẩn đơn vị vận hành (VD Westin Design Standards)",
    "CONTRACT": "Hợp đồng và phụ lục",
    "LEGAL_STANDARD": "TCVN / QCVN – 1 › 1.1 › Phụ lục A",
}
STREAMS: dict[str, str] = {
    "LEGAL_STANDARD": "TCVN/QCVN – nhà nước ban hành",
    "OPERATOR_STANDARD": "Tiêu chuẩn đơn vị vận hành",
    "DESIGN_CONSULTANT": "Thuyết minh, Spec, Chỉ dẫn KT của tư vấn",
    "CONTRACT": "Hợp đồng & phụ lục",
}


@lru_cache(maxsize=1)
def knowledge_cfg() -> dict[str, Any]:
    cfg = {**DEFAULTS, **load_yaml("knowledge")}
    cfg["worker"] = {**DEFAULTS["worker"], **(cfg.get("worker") or {})}
    return cfg


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def uploads_dir() -> Path:
    return _path(knowledge_cfg()["uploads_dir"])


def artifacts_dir() -> Path:
    return _path(knowledge_cfg()["artifacts_dir"])


def max_upload_bytes() -> int:
    return int(float(knowledge_cfg()["max_upload_mb"]) * 1024 * 1024)


def stages() -> list[str]:
    """Giai đoạn: giá trị trong config/ingestion.yaml › metadata.stage_patterns."""
    vals = [v for _, v in (load_yaml("ingestion").get("metadata") or {}).get("stage_patterns", [])]
    return list(dict.fromkeys(vals))


def options() -> dict[str, Any]:
    profiles = load_yaml("headings").get("profiles") or {}
    return {
        "streams": [{"value": k, "label": v} for k, v in STREAMS.items() if k in Stream.__members__],
        "disciplines": [d.value for d in Discipline],
        "doc_types": [{"value": k, "label": DOC_TYPES.get(k, k)} for k in profiles],
        "stages": stages(),
        "max_upload_mb": knowledge_cfg()["max_upload_mb"],
    }
