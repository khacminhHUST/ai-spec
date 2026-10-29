"""Cấu hình Agent Harness: config/agent.yaml, config/mcp.yaml (không hard-code tham số)."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import load_yaml


@lru_cache(maxsize=1)
def agent_cfg() -> dict[str, Any]:
    return load_yaml("agent")


@lru_cache(maxsize=1)
def mcp_cfg() -> dict[str, Any]:
    return load_yaml("mcp")
