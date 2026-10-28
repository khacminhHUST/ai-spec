"""Hợp đồng dữ liệu Ingestion ↔ Query (theo docs/ingestion.md – "Schema đầu ra").

Không tự đổi schema. Cần đổi → ghi đề xuất và hỏi lại.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.keys import make_parent_id


class Stream(StrEnum):
    LEGAL_STANDARD = "LEGAL_STANDARD"
    OPERATOR_STANDARD = "OPERATOR_STANDARD"
    DESIGN_CONSULTANT = "DESIGN_CONSULTANT"
    CONTRACT = "CONTRACT"


class Discipline(StrEnum):
    HVAC = "HVAC"
    ELECTRICAL = "ELECTRICAL"
    ELV = "ELV"
    PLUMBING = "PLUMBING"
    FIRE = "FIRE"
    LIFT = "LIFT"
    BMS = "BMS"
    GENERAL = "GENERAL"


class Lang(StrEnum):
    vi = "vi"
    en = "en"


class ParentType(StrEnum):
    SECTION = "SECTION"
    TABLE = "TABLE"


class ChildType(StrEnum):
    TEXT = "TEXT"
    TABLE_ROWS = "TABLE_ROWS"


class _Model(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid")


class Document(_Model):
    """Postgres `documents`. Trường không suy ra được → None + warning."""

    doc_id: str
    doc_code: str | None = None
    project_id: str | None = None
    building: str | None = None
    stream: Stream | None = None
    disciplines: list[Discipline] = Field(default_factory=list)
    title: str | None = None
    issuer: str | None = None
    revision: str | None = None
    issue_date: date | None = None
    stage: str | None = None
    languages: list[Lang] = Field(default_factory=list)
    is_bilingual: bool = False
    is_latest: bool = True
    source_path: str
    file_hash: str
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = Field(default_factory=list)


class ParentChunk(_Model):
    """Postgres `parent_chunks`. `parent_id` = make_parent_id(doc_id, lang, section_key)."""

    parent_id: str
    doc_id: str
    section_key: str
    parent_section_key: str | None = None
    lang: Lang
    type: ParentType
    number: str | None = None
    title: str | None = None
    breadcrumb: str = ""
    content_md: str
    page_start: int | None = None
    page_end: int | None = None
    referenced_ids: list[str] = Field(default_factory=list)
    token_count: int = 0

    @model_validator(mode="after")
    def _check_parent_id(self) -> "ParentChunk":
        expected = make_parent_id(self.doc_id, self.lang, self.section_key)
        if self.parent_id != expected:
            raise ValueError(f"parent_id phải là {expected!r}, nhận {self.parent_id!r}")
        return self


# Trường payload dùng để lọc trên Qdrant (có payload index)
PAYLOAD_FILTER_FIELDS = ("project_id", "building", "stream", "disciplines", "doc_id", "revision", "is_latest", "lang")


class ChildChunk(_Model):
    """Qdrant `child_chunks_vi` / `child_chunks_en`.

    - `text`: có breadcrumb ở đầu (dùng để embed). `raw_text`: text gốc.
    - `child_id`: make_child_id(parent_id, seq) – seq liên tục trong parent (dùng lấy child lân cận).
    - Vector `dense`, `sparse` không nằm trong model (truyền riêng khi upsert).
    """

    # nội dung
    child_id: str
    parent_id: str
    section_key: str
    lang: Lang
    type: ChildType
    text: str
    raw_text: str
    topic: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    # payload lọc
    project_id: str | None = None
    building: str | None = None
    stream: Stream | None = None
    disciplines: list[Discipline] = Field(default_factory=list)
    doc_id: str
    revision: str | None = None
    is_latest: bool = True

    @model_validator(mode="after")
    def _check_ids(self) -> "ChildChunk":
        if not self.child_id.startswith(self.parent_id + "#"):
            raise ValueError("child_id phải có dạng '<parent_id>#<seq>' (dùng make_child_id)")
        if self.parent_id != make_parent_id(self.doc_id, self.lang, self.section_key):
            raise ValueError("parent_id không khớp doc_id/lang/section_key")
        if not self.text.strip() or not self.raw_text.strip():
            raise ValueError("child rỗng")
        return self

    @property
    def point_id(self) -> str:
        return child_point_id(self.child_id)

    def qdrant_payload(self) -> dict:
        return self.model_dump(mode="json")


_POINT_NS = uuid.UUID("6f1c2a52-0b7e-4f7a-9d39-5e0c3b8e2a10")


def child_point_id(child_id: str) -> str:
    """Qdrant point id (UUID ổn định) từ child_id."""
    return str(uuid.uuid5(_POINT_NS, child_id))
