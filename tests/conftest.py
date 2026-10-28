"""Fixture & quy tắc skip dùng chung.

- `integration` / `e2e`: skip nếu Postgres/Qdrant chưa chạy (docker compose up -d).
- `e2e`: skip thêm nếu thiếu GOOGLE_API_KEY.
- `test_ns`: namespace riêng (schema Postgres + collection Qdrant), migrate trước, drop sau.
"""
from __future__ import annotations

import socket
import uuid
from functools import lru_cache
from urllib.parse import urlparse

import pytest

from app.core.config import Settings, get_settings


@lru_cache(maxsize=1)
def services_up() -> bool:
    s = get_settings()
    for url in (s.postgres_url, s.qdrant_url):
        u = urlparse(url)
        try:
            socket.create_connection((u.hostname, u.port), timeout=1).close()
        except OSError:
            return False
    return True


def pytest_collection_modifyitems(config, items):
    s = get_settings()
    no_svc = pytest.mark.skip(reason="Postgres/Qdrant chưa chạy (docker compose up -d)")
    no_key = pytest.mark.skip(reason="Thiếu GOOGLE_API_KEY")
    for item in items:
        if ("integration" in item.keywords or "e2e" in item.keywords) and not services_up():
            item.add_marker(no_svc)
        if "e2e" in item.keywords and not s.google_api_key:
            item.add_marker(no_key)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def mock_settings(settings) -> Settings:
    """Settings ép mock mọi provider (dùng cho unit test)."""
    import dataclasses

    return dataclasses.replace(settings, mock=frozenset({"all"}))


@pytest.fixture
def test_ns(settings):
    """Namespace tạm đã migrate; tự drop sau test."""
    from app.db.migrate import drop, migrate

    s = settings.with_ns(f"t_{uuid.uuid4().hex[:8]}")
    migrate(s)
    yield s
    drop(s)
