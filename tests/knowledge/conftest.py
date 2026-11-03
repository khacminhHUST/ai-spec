"""Fixture dùng chung cho test kho tri thức."""
from __future__ import annotations

import dataclasses
import os

import pytest

# Docling đọc model từ cache HF (không gọi mạng) – như tests/ingestion
os.environ.setdefault("HF_HUB_OFFLINE", "1")


@pytest.fixture
def kn_ns(test_ns):
    """Namespace tạm, mock mọi provider (LLM chunking + embedding không gọi mạng)."""
    return dataclasses.replace(test_ns, mock=frozenset({"all"}))


@pytest.fixture
def store(tmp_path):
    from app.services.knowledge.storage import LocalFileStore

    return LocalFileStore(tmp_path / "uploads")
