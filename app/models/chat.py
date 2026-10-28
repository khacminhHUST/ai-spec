"""Schema request/response của API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    user_id: str
    question: str = Field(min_length=1)
