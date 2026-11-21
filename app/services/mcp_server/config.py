"""Cấu hình MCP server: config/mcp_server.yaml, biến môi trường ghi đè (docs/mcp-server.md mục 5)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.core.config import load_yaml

PRM_PREFIX = "/.well-known/oauth-protected-resource"


@dataclass(frozen=True)
class ServerConfig:
    enabled: bool
    path: str                  # đường dẫn MCP trong app
    resource_url: str          # URL công khai (RFC 8707), cũng là 1 giá trị `aud` chấp nhận
    server_name: str           # tên đăng ký ở user-management, giá trị `aud` thứ hai
    issuer: str
    jwks_url: str
    leeway_s: int
    require_permission: str
    result_max_tokens: int
    public_key_pem: str        # "" → khóa nhúng sẵn (planner_key.py)

    @property
    def audiences(self) -> list[str]:
        return [a for a in (self.resource_url, self.server_name) if a]

    @property
    def prm_path(self) -> str:
        """Đường dẫn Protected Resource Metadata (RFC 9728 §3.1): chèn well-known trước path của resource_url."""
        path = urlparse(self.resource_url).path
        return PRM_PREFIX + ("" if path in ("", "/") else path)

    @property
    def prm_url(self) -> str:
        u = urlparse(self.resource_url)
        return f"{u.scheme}://{u.netloc}{self.prm_path}"

    def jwks(self) -> str:
        return self.jwks_url or (self.issuer.rstrip("/") + "/.well-known/jwks.json")


def _env(name: str, default: Any) -> Any:
    v = os.environ.get(name)
    return default if v is None else v


def load_config(overrides: dict[str, Any] | None = None) -> ServerConfig:
    c = {**load_yaml("mcp_server"), **(overrides or {})}
    enabled = str(_env("MCP_SERVER_ENABLED", c.get("enabled", True))).strip().lower() not in ("0", "false", "no", "")
    return ServerConfig(
        enabled=enabled,
        path="/" + str(c.get("path", "/kb-mcp")).strip("/"),
        resource_url=str(_env("MCP_SERVER_RESOURCE_URL", c["resource_url"])).rstrip("/"),
        server_name=str(c.get("server_name", "")),
        issuer=str(c["issuer"]).rstrip("/"),
        jwks_url=str(c.get("jwks_url") or ""),
        leeway_s=int(c.get("leeway_s", 30)),
        require_permission=str(_env("MCP_SERVER_REQUIRE_PERMISSION", c.get("require_permission", "read")) or "").strip(),
        result_max_tokens=int(c.get("result_max_tokens", 20000)),
        public_key_pem=str(_env("MCP_SERVER_PUBLIC_KEY_PEM", c.get("public_key_pem", "")) or "").replace("\\n", "\n"),
    )
