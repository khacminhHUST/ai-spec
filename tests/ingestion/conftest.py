"""Fixture dùng chung cho test ingestion."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# unit test không gọi mạng: tokenizer/model Docling đọc từ cache HF
os.environ.setdefault("HF_HUB_OFFLINE", "1")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ingestion"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def mock_llm():
    from app.providers.llm import MockChatModel

    return MockChatModel(responses=["không phải JSON"])


@pytest.fixture(scope="session")
def s5_root():
    from app.core.config import get_settings

    s = get_settings()
    root = s.data_root / s.project_list()[0]["buildings"][0]["source_root"]
    if not root.exists():
        pytest.skip(f"Không có bộ tài liệu S5: {root}")
    return root
