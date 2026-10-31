"""Request/response của phần Agent Harness (docs/agent-harness.md mục 7)."""
from __future__ import annotations

from pydantic import BaseModel


class ConfirmRequest(BaseModel):
    conversation_id: str
    user_id: str
    confirm_id: str
    approve: bool


class UploadedFile(BaseModel):
    name: str
    size: int
