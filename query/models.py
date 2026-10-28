"""Kiểu dữ liệu nội bộ Query Pipeline (docs/query-pipeline.md mục 2–3).

State LangGraph chỉ chứa dict/list thuần (an toàn khi serialize checkpoint); các model dưới đây dùng
để validate / tạo dict.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.schema import Discipline, Stream

STREAMS = [s.value for s in Stream]
DISCIPLINES = [d.value for d in Discipline]
Status = Literal["OK", "PARTIAL", "NOT_FOUND", "ASK_CLARIFY"]
IntentType = Literal["TECHNICAL_SPEC_LOOKUP", "COMPARE", "CONTRACT_TERM", "STANDARD_LOOKUP", "OTHER"]


def _dedupe_str(v: list) -> list[str]:
    return list(dict.fromkeys(str(x).strip() for x in (v or []) if str(x).strip()))


class RouterOutput(BaseModel):
    """Output N1 (Fast LLM, JSON). Giá trị ngoài enum bị loại bỏ (không fail)."""

    model_config = ConfigDict(extra="ignore")

    rewritten_question: str
    project_action: Literal["set", "add", "remove", "keep"] = "keep"
    project_mentions: list[str] = Field(default_factory=list)
    disciplines: list[str] = Field(default_factory=list)
    intent_type: IntentType = "OTHER"
    streams: list[str] = Field(default_factory=list)
    queries_vi: list[str] = Field(default_factory=list)
    queries_en: list[str] = Field(default_factory=list)
    exact_keywords: list[str] = Field(default_factory=list)

    @field_validator("project_mentions", "queries_vi", "queries_en", "exact_keywords", mode="before")
    @classmethod
    def _clean(cls, v):
        return _dedupe_str(v)

    @field_validator("disciplines", mode="before")
    @classmethod
    def _disc(cls, v):
        return [d for d in _dedupe_str([str(x).upper() for x in v or []]) if d in DISCIPLINES]

    @field_validator("streams", mode="before")
    @classmethod
    def _streams(cls, v):
        return [s for s in _dedupe_str([str(x).upper() for x in v or []]) if s in STREAMS]

    @field_validator("project_action", mode="before")
    @classmethod
    def _action(cls, v):
        v = str(v or "keep").lower()
        return v if v in ("set", "add", "remove", "keep") else "keep"

    @field_validator("intent_type", mode="before")
    @classmethod
    def _intent(cls, v):
        v = str(v or "OTHER").upper()
        return v if v in IntentType.__args__ else "OTHER"  # type: ignore[attr-defined]

    @classmethod
    def fallback(cls, question: str) -> "RouterOutput":
        """Lỗi parse JSON: keep, mọi discipline/stream, queries_vi = [question]."""
        return cls(rewritten_question=question, project_action="keep", queries_vi=[question])


class ChildHit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    child_id: str
    parent_id: str
    doc_id: str
    section_key: str
    lang: str
    stream: str | None = None
    project_id: str | None = None
    text: str
    raw_text: str = ""
    page_start: int | None = None
    page_end: int | None = None
    fusion_score: float = 0.0
    rerank_score: float | None = None


class ContextBlock(BaseModel):
    n: int = 0                      # số thứ tự trong context (LLM trích dẫn bằng [n])
    project_id: str | None
    stream: str | None
    priority: int = 99
    doc_id: str
    doc_title: str | None = None
    revision: str | None = None
    section_key: str
    number: str | None = None
    title: str | None = None
    vi: str | None = None
    en: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    is_reference: bool = False
    referenced_by: str | None = None
    score: float = 0.0
    unresolved_refs: list[str] = Field(default_factory=list)  # tham chiếu "chưa có trong hệ thống"
    tokens: int = 0

    @property
    def langs(self) -> list[str]:
        return [lang for lang in ("vi", "en") if getattr(self, lang)]

    def text(self) -> str:
        return "\n".join(x for x in (self.vi, self.en) if x)


class Citation(BaseModel):
    n: int
    doc_id: str
    doc_title: str | None = None
    revision: str | None = None
    section_key: str
    number: str | None = None
    title: str | None = None
    page: int | None = None
    page_end: int | None = None
    lang: list[str] = Field(default_factory=list)
