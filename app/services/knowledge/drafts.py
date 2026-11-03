"""Kết quả nháp của job: artifact của ingestion (write_artifacts) + document.json – đọc lại khi duyệt / phát hành.

Thư mục: <artifacts_dir>/<job_id>/<doc_id>/ gồm parsed.json, vi.md, en.md, parents.jsonl, children.jsonl,
report.json (pipeline.write_artifacts) và document.json (Document – để phát hành không cần parse lại).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.document import ChildChunk, Document, ParentChunk
from app.services.ingestion.pipeline import IngestResult, write_artifacts

DOCUMENT_FILE = "document.json"


@dataclass
class Draft:
    document: Document
    parents: list[ParentChunk]
    children: list[ChildChunk]


def save_draft(res: IngestResult, out_root: Path) -> Path:
    d = write_artifacts(res, out_root)
    (d / DOCUMENT_FILE).write_text(res.document.model_dump_json(indent=1), encoding="utf-8")
    return d


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_draft(artifact_dir: Path) -> Draft:
    """Đọc lại kết quả nháp; ingested_at = thời điểm phát hành."""
    d = Path(artifact_dir)
    if not (d / DOCUMENT_FILE).exists():
        raise FileNotFoundError(f"không còn kết quả nháp ở {d} – chạy lại job")
    doc = Document.model_validate_json((d / DOCUMENT_FILE).read_text(encoding="utf-8"))
    doc = doc.model_copy(update={"ingested_at": datetime.now(timezone.utc), "is_latest": True})
    parents = [ParentChunk(**p) for p in _jsonl(d / "parents.jsonl")]
    children = [ChildChunk(**c) for c in _jsonl(d / "children.jsonl")]
    return Draft(doc, parents, children)


def read_report(artifact_dir: Path) -> dict[str, Any]:
    return json.loads((Path(artifact_dir) / "report.json").read_text(encoding="utf-8"))


def sections(artifact_dir: Path) -> list[dict[str, Any]]:
    """Cây mục: parent (cả SECTION và TABLE) theo thứ tự xuất hiện, kèm số child."""
    d = Path(artifact_dir)
    n_kids: dict[str, int] = {}
    for c in _jsonl(d / "children.jsonl"):
        n_kids[c["parent_id"]] = n_kids.get(c["parent_id"], 0) + 1
    keep = ("lang", "section_key", "parent_section_key", "type", "number", "title", "page_start", "page_end",
            "token_count")
    return [{**{k: p.get(k) for k in keep}, "n_children": n_kids.get(p["parent_id"], 0)}
            for p in _jsonl(d / "parents.jsonl")]


def section_preview(artifact_dir: Path, lang: str, section_key: str) -> dict[str, Any] | None:
    """Parent + các child của 1 mục; không có → None."""
    d = Path(artifact_dir)
    parent = next((p for p in _jsonl(d / "parents.jsonl") if p["lang"] == lang and p["section_key"] == section_key),
                  None)
    if not parent:
        return None
    kids = [c for c in _jsonl(d / "children.jsonl") if c["parent_id"] == parent["parent_id"]]
    keep = ("child_id", "type", "topic", "raw_text", "text", "page_start", "page_end")
    return {"parent": parent, "children": [{k: c.get(k) for k in keep} for c in kids]}
