"""Registry connector: DB (khai qua giao diện) + config/mcp.yaml (chỉ đọc). Tạo/sửa/xóa, mã hóa header tĩnh.

Connector trong DB có 2 loại:
- cá nhân (`shared = false`): thuộc riêng người tạo (`created_by` = user_id/email) – chỉ người đó thấy, sửa, kết nối, dùng;
- dùng chung cho tổ chức (`shared = true`, migration 007_mcp_org.sql): mọi người thấy và tự bấm Kết nối (token riêng
  từng người); chỉ admin sửa / xóa / đặt quyền tool (kiểm quyền ở API).
Connector config/mcp.yaml dùng chung cho mọi người (chỉ đọc).

Connector `oauth` khi tạo/sửa URL: chạy khám phá (mục 2.1) + đảm bảo có client OAuth (DCR 1 lần hoặc nhập tay).
Header tĩnh lưu mã hóa (JSON), cho phép `${ENV_VAR}` – thay bằng giá trị trong .env lúc gọi.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable

import httpx

from app.services.agent.mcp.oauth.client_registry import ClientRegistry
from app.services.agent.mcp.oauth.crypto import SecretBox
from app.services.agent.mcp.oauth.discovery import check_url, discover
from app.services.agent.mcp.oauth.errors import OAuthError
from app.services.agent.mcp.oauth.store import OAuthStore
from app.services.agent.mcp.store import ConnectorStore
from app.services.agent.mcp.yaml_servers import _subst

NAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")
AUTH_TYPES = ("oauth", "none", "headers")
TRANSPORTS = ("streamable_http", "sse")
YAML_PREFIX = "yaml:"
DESCRIPTION_MAX = 300                 # = describe.DESC_MAX_CHARS (mô tả tự sinh)
EDITABLE = ("name", "display_name", "description", "url", "transport", "auth_type", "enabled", "headers", "client_id",
            "client_secret", "shared")


class ConnectorError(ValueError):
    """Dữ liệu connector không hợp lệ (API → 400)."""


def _norm_headers(headers: Any) -> dict[str, str]:
    if isinstance(headers, list):
        headers = {str(h.get("name", "")).strip(): str(h.get("value", "")) for h in headers}
    out = {str(k).strip(): str(v) for k, v in (headers or {}).items() if str(k).strip()}
    return out


class ConnectorRegistry:
    def __init__(self, store: ConnectorStore, oauth_store: OAuthStore, clients: ClientRegistry,
                 box: Callable[[], SecretBox], http: Callable[[], httpx.Client], cfg: dict[str, Any], redirect_uri: str):
        self.store = store
        self.oauth_store = oauth_store
        self.clients = clients
        self.box = box
        self.http = http
        self.cfg = cfg
        self.redirect_uri = redirect_uri

    # ---------------------------------------------------------------- đọc
    def yaml_connectors(self) -> list[dict[str, Any]]:
        out = []
        for name, s in (self.cfg.get("servers") or {}).items():
            s = s or {}
            target = s.get("url") or " ".join([str(s.get("command", "")), *map(str, s.get("args") or [])]).strip()
            out.append({"connector_id": YAML_PREFIX + name, "name": name, "display_name": name,
                        "description": s.get("description", ""), "url": target,
                        "transport": s.get("transport", ""), "auth_type": "headers" if s.get("headers") else "none",
                        "enabled": True, "source": "yaml", "readonly": True,
                        "header_names": sorted((s.get("headers") or {}).keys())})
        return out

    def list_db(self, owner: str) -> list[dict[str, Any]]:
        """Connector trong DB người đó thấy: dùng chung + cá nhân của họ (dùng chung xếp trước)."""
        return self.store.list_connectors(owner) if owner else []

    def usable(self, owner: str) -> list[dict[str, Any]]:
        """Như list_db nhưng mỗi tên 1 connector (tên = tiền tố tool của agent): trùng tên → connector dùng chung thắng
        (connector cá nhân tạo trước khi admin đưa connector cùng tên lên dùng chung)."""
        out: dict[str, dict[str, Any]] = {}
        for r in self.list_db(owner):
            out.setdefault(r["name"], r)
        return list(out.values())

    def get(self, connector_id: str) -> dict[str, Any] | None:
        """Connector trong DB, không kiểm chủ sở hữu (yaml → None: chỉ đọc, không qua registry này)."""
        return None if connector_id.startswith(YAML_PREFIX) else self.store.get_connector(connector_id)

    def owned(self, connector_id: str, owner: str) -> dict[str, Any] | None:
        """Connector trong DB nếu thuộc `owner`; của người khác → None (như không tồn tại)."""
        row = self.get(connector_id)
        return row if row is not None and owner and row.get("created_by") == owner else None

    def visible(self, connector_id: str, user: str) -> dict[str, Any] | None:
        """Connector user thấy được (dùng chung, hoặc cá nhân của chính user) – để kết nối, xem tool, dùng."""
        row = self.get(connector_id)
        if row is None or not user:
            return None
        return row if row.get("shared") or row.get("created_by") == user else None

    @staticmethod
    def can_manage(row: dict[str, Any], user: str, is_admin: bool) -> bool:
        """Sửa / xóa / đặt quyền tool: dùng chung → admin; cá nhân → chủ sở hữu."""
        return is_admin if row.get("shared") else bool(user) and row.get("created_by") == user

    def header_names(self, row: dict[str, Any]) -> list[str]:
        if not row.get("headers_enc"):
            return []
        try:
            return sorted(json.loads(self.box().decrypt(row["headers_enc"])))
        except OAuthError:
            return []

    def headers_for(self, row: dict[str, Any]) -> dict[str, str]:
        """Header đã giải mã + thay ${ENV_VAR}. Thiếu MEP_SECRET_KEY → SecretKeyMissing."""
        if not row.get("headers_enc"):
            return {}
        return _subst(json.loads(self.box().decrypt(row["headers_enc"])))

    # ---------------------------------------------------------------- ghi
    def _check_name(self, name: str, owner: str, exclude_id: str | None = None) -> str:
        name = (name or "").strip()
        if not NAME_RE.match(name):
            raise ConnectorError("Tên connector chỉ gồm a-z, 0-9, _ (2–32 ký tự)")
        if name in (self.cfg.get("servers") or {}):
            raise ConnectorError(f"Tên '{name}' trùng server trong config/mcp.yaml")
        if any(c["name"] == name and c["connector_id"] != exclude_id for c in self.store.list_connectors(owner)):
            raise ConnectorError(f"Tên connector đã tồn tại: {name}")
        return name

    def _oauth_fields(self, url: str, client_id: str | None, client_secret: str | None) -> dict[str, Any]:
        self.box()                                           # thiếu MEP_SECRET_KEY → báo ngay
        d = discover(url, self.http(), client_name=self.cfg.get("client_name", "MEP Project Assistant"))
        if d.auth_type != "oauth":
            raise ConnectorError("Server trả lời không cần token – chọn kiểu 'Không xác thực'")
        if client_id:
            self.clients.save_manual(d.issuer, d.as_metadata, self.redirect_uri, client_id, client_secret)
        else:
            self.clients.ensure(d.issuer, d.as_metadata, self.redirect_uri)
        return {"resource": d.resource, "issuer": d.issuer, "as_metadata": d.as_metadata,
                "scopes": " ".join(d.scopes) if d.scopes else None}

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "url" in data:
            out["url"] = check_url(data["url"], bool(self.cfg.get("allow_http_localhost", True)))
        if "transport" in data:
            if data["transport"] not in TRANSPORTS:
                raise ConnectorError(f"transport phải là {' | '.join(TRANSPORTS)}")
            out["transport"] = data["transport"]
        if "auth_type" in data:
            if data["auth_type"] not in AUTH_TYPES:
                raise ConnectorError(f"auth_type phải là {' | '.join(AUTH_TYPES)}")
            out["auth_type"] = data["auth_type"]
        if "display_name" in data:
            out["display_name"] = str(data["display_name"] or "").strip()
        if "description" in data:
            out["description"] = " ".join(str(data["description"] or "").split())[:DESCRIPTION_MAX]
        if "enabled" in data:
            out["enabled"] = bool(data["enabled"])
        if "shared" in data:
            out["shared"] = bool(data["shared"])
        return out

    def create(self, data: dict[str, Any], created_by: str, shared: bool = False) -> dict[str, Any]:
        """shared=True: connector dùng chung (API chỉ cho admin)."""
        created_by = (created_by or "").strip()
        if not created_by:
            raise ConnectorError("Thiếu user_id (chủ sở hữu connector)")
        f = self._validate({"transport": "streamable_http", **data})
        if "url" not in f or "auth_type" not in f:
            raise ConnectorError("Thiếu url hoặc auth_type")
        name = self._check_name(data.get("name", ""), created_by)
        row: dict[str, Any] = {"connector_id": uuid.uuid4().hex, "name": name, "display_name": f.get("display_name") or name,
                               "description": f.get("description", ""),
                               "url": f["url"], "transport": f["transport"], "auth_type": f["auth_type"],
                               "enabled": f.get("enabled", True), "created_by": created_by, "shared": bool(shared)}
        row.update(self._auth_fields(f["auth_type"], f["url"], data, None))
        try:
            self.store.insert_connector(row)
        except ValueError as e:
            raise ConnectorError(str(e)) from None
        return self.store.get_connector(row["connector_id"])

    def _auth_fields(self, auth_type: str, url: str, data: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
        """Cột phụ thuộc kiểu xác thực (headers_enc, resource, issuer, as_metadata, scopes)."""
        blank = {"headers_enc": None, "resource": None, "issuer": None, "as_metadata": None, "scopes": None}
        if auth_type == "headers":
            headers = _norm_headers(data.get("headers"))
            if not headers and old and old.get("headers_enc") and data.get("headers") is None:
                return {**blank, "headers_enc": old["headers_enc"]}
            if not headers:
                raise ConnectorError("Kiểu 'Header tĩnh' cần ít nhất 1 header")
            return {**blank, "headers_enc": self.box().encrypt(json.dumps(headers, ensure_ascii=False))}
        if auth_type == "oauth":
            return {**blank, **self._oauth_fields(url, data.get("client_id"), data.get("client_secret"))}
        return blank

    def update(self, connector_id: str, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """→ (connector mới, có phải xóa token cũ không – đổi URL/kiểu xác thực)."""
        old = self.get(connector_id)
        if old is None:
            raise KeyError(connector_id)
        data = {k: v for k, v in data.items() if k in EDITABLE and v is not None}
        f = self._validate(data)
        if "name" in data or f.get("shared"):       # đổi tên, hoặc đưa lên dùng chung → kiểm trùng tên
            f["name"] = self._check_name(data.get("name", old["name"]), old["created_by"], exclude_id=connector_id)
        auth_type, url = f.get("auth_type", old["auth_type"]), f.get("url", old["url"])
        reset = auth_type != old["auth_type"] or url != old["url"]
        reauth = reset or (auth_type == "headers" and "headers" in data) or (
            auth_type == "oauth" and bool(data.get("client_id")))
        if reauth:
            f.update(self._auth_fields(auth_type, url, data, old))
        try:
            self.store.update_connector(connector_id, **f)
        except ValueError as e:
            raise ConnectorError(str(e)) from None
        if reset:
            self.oauth_store.delete_connector_tokens(connector_id)
        return self.store.get_connector(connector_id), reset

    def delete(self, connector_id: str) -> None:
        if self.get(connector_id) is None:
            raise KeyError(connector_id)
        self.store.delete_connector(connector_id)
