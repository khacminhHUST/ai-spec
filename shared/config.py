"""Config loader: config/*.yaml + .env.

Biến môi trường:
- MEP_NS: namespace → Postgres schema + tiền tố collection Qdrant (rỗng = public / không tiền tố).
- MEP_MOCK: provider dùng mock, phân cách phẩy (deepinfra, google, all). Thiếu key → tự mock.
- MEP_DATA_ROOT: thư mục gốc chứa tài liệu mẫu (source_root trong projects.yaml tính từ đây).
"""
from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

# provider trong config → nhóm key/mock
_PROVIDER_GROUP = {"deepinfra": "deepinfra", "google": "google", "google_genai": "google"}
_NS_RE = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def load_yaml(name: str) -> dict[str, Any]:
    """Đọc config/<name>.yaml (không có file → {})."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def provider_group(provider: str) -> str:
    try:
        return _PROVIDER_GROUP[provider]
    except KeyError:
        raise ValueError(f"provider không hỗ trợ: {provider}") from None


@dataclass(frozen=True)
class Settings:
    models: dict[str, Any]
    pipeline: dict[str, Any]
    projects: dict[str, Any]
    google_api_key: str | None
    deepinfra_api_key: str | None
    postgres_url: str
    qdrant_url: str
    ns: str
    mock: frozenset[str]
    data_root: Path

    # ---- namespace ----
    @property
    def pg_schema(self) -> str:
        return self.ns or "public"

    def collection(self, lang: str) -> str:
        """Tên collection Qdrant theo ngôn ngữ (đã gắn tiền tố namespace)."""
        base = self.models["embedding"][lang]["collection"]
        return f"{self.ns}__{base}" if self.ns else base

    def with_ns(self, ns: str) -> "Settings":
        return dataclasses.replace(self, ns=_check_ns(ns))

    # ---- provider / mock ----
    def api_key(self, provider: str) -> str | None:
        group = provider_group(provider)
        return self.google_api_key if group == "google" else self.deepinfra_api_key

    def is_mock(self, provider: str) -> bool:
        group = provider_group(provider)
        return "all" in self.mock or group in self.mock or not self.api_key(provider)

    # ---- tiện ích ----
    def embedding_cfg(self, lang: str) -> dict[str, Any]:
        return self.models["embedding"][lang]

    def project_list(self) -> list[dict[str, Any]]:
        return self.projects.get("projects", [])


def _check_ns(ns: str) -> str:
    ns = (ns or "").strip().lower()
    if ns and not _NS_RE.match(ns):
        raise ValueError(f"MEP_NS không hợp lệ: {ns!r} (chỉ a-z, 0-9, _)")
    return ns


def _env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    mock = frozenset(p.strip().lower() for p in (os.environ.get("MEP_MOCK") or "").split(",") if p.strip())
    return Settings(
        models=load_yaml("models"),
        pipeline=load_yaml("pipeline"),
        projects=load_yaml("projects"),
        google_api_key=_env("GOOGLE_API_KEY"),
        deepinfra_api_key=_env("DEEPINFRA_API_KEY"),
        postgres_url=_env("POSTGRES_URL") or "postgresql://mep:mep@localhost:5442/mep",
        qdrant_url=_env("QDRANT_URL") or "http://localhost:6333",
        ns=_check_ns(os.environ.get("MEP_NS", "")),
        mock=mock,
        data_root=Path(_env("MEP_DATA_ROOT") or ROOT),
    )
