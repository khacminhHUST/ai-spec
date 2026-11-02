"""Schema request/response của API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    user_id: str
    question: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)     # tên file đã upload (POST /conversations/{id}/files)
    force_route: Literal["lookup", "agent"] | None = None
