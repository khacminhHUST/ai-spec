"""API MCP connector (docs/mcp-connectors.md mục 5). Mọi route theo connector nhận `user_id`.
- Connector cá nhân: chỉ chủ sở hữu thấy / dùng / sửa (của người khác → 404 như không tồn tại).
- Connector dùng chung (`shared`): ai cũng thấy, kết nối, xem tool, thử; chỉ admin sửa / xóa / đặt quyền tool (403).
Admin: chạy sau web Tri thức → `role` = ADMIN trong token (TrustedProxyAuth đặt request.state.user_role);
chạy local (không có TrustedProxyAuth) → ai cũng là admin.

`user_id`: chạy sau web Tri thức (MEP_AUTH_MODE=trusted_proxy) là email trong token, trình duyệt không sửa được;
chạy local là giá trị client tự gửi.
Callback OAuth đi qua origin của frontend (proxy /mcp) nên redirect_uri = public_base_url + /mcp/oauth/callback.
"""
from __future__ import annotations

import html
import json
import logging
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.deps import get_runtime
from app.services.agent.mcp.oauth.errors import OAuthError, SecretKeyMissing
from app.services.agent.runtime import AgentRuntime

log = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])
USER_COOKIE = "mep_oauth_uid"


class HeaderItem(BaseModel):
    name: str
    value: str = ""


class ConnectorIn(BaseModel):
    name: str
    display_name: str | None = None
    description: str | None = None      # trống → tự điền ở lần thử kết nối thành công đầu tiên (/test, OAuth callback)
    url: str
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    auth_type: Literal["oauth", "none", "headers"]
    headers: list[HeaderItem] | None = None
    client_id: str | None = None
    client_secret: str | None = None
    enabled: bool = True
    shared: bool = False                # dùng chung cho cả tổ chức (chỉ admin)
    user_id: str                        # người tạo = chủ sở hữu (created_by)


class ConnectorPatch(BaseModel):
    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    url: str | None = None
    transport: Literal["streamable_http", "sse"] | None = None
    auth_type: Literal["oauth", "none", "headers"] | None = None
    headers: list[HeaderItem] | None = None
    client_id: str | None = None
    client_secret: str | None = None
    enabled: bool | None = None
    shared: bool | None = None          # đưa lên / gỡ khỏi dùng chung (chỉ admin)


class DiscoverIn(BaseModel):
    url: str


class AccessIn(BaseModel):
    access: Literal["read", "write", "disabled"] | None = None     # None → bỏ ghi đè


def get_hub(runtime: AgentRuntime = Depends(get_runtime)):
    if runtime.mcp is None:
        raise HTTPException(503, "MCP connector chưa khởi tạo (kiểm tra Postgres và migration 002_mcp)")
    return runtime.mcp


def _call(fn, *args, **kwargs):
    """Lỗi nghiệp vụ → HTTP: dữ liệu sai 400, không thấy 404, thiếu MEP_SECRET_KEY 503."""
    from app.services.agent.mcp.registry import ConnectorError

    try:
        return fn(*args, **kwargs)
    except SecretKeyMissing as e:
        raise HTTPException(503, {"code": e.code, "message": e.message}) from None
    except OAuthError as e:
        raise HTTPException(400, {"code": e.code, "message": e.message}) from None
    except ConnectorError as e:
        raise HTTPException(400, {"code": "invalid_connector", "message": str(e)}) from None
    except KeyError:
        raise HTTPException(404, "không tìm thấy connector") from None


def is_admin(request: Request) -> bool:
    """Sau web Tri thức: role ADMIN trong token. Local (không qua TrustedProxyAuth): luôn True."""
    if getattr(request.state, "user_email", None) is None:
        return True
    return getattr(request.state, "user_role", "") == "ADMIN"


def _db_row(hub, connector_id: str, user_id: str) -> dict[str, Any]:
    """Connector user thấy được (dùng chung hoặc của chính user)."""
    if hub.is_yaml(connector_id):
        raise HTTPException(400, "Connector từ config/mcp.yaml chỉ đọc – sửa trong file cấu hình")
    row = hub.registry.visible(connector_id, user_id)
    if row is None:
        raise HTTPException(404, "không tìm thấy connector")
    return row


def _managed_row(hub, connector_id: str, user_id: str, admin: bool) -> dict[str, Any]:
    """Connector user được sửa / xóa / đặt quyền tool; dùng chung mà không phải admin → 403."""
    row = _db_row(hub, connector_id, user_id)
    if not hub.registry.can_manage(row, user_id, admin):
        raise HTTPException(403, "Connector dùng chung của tổ chức – chỉ quản trị viên được thay đổi")
    return row


def _headers(items: list[HeaderItem] | None) -> list[dict[str, str]] | None:
    return None if items is None else [h.model_dump() for h in items]


# ------------------------------------------------------------------ connector

@router.get("/connectors")
def list_connectors(request: Request, user_id: str = Query(...), hub=Depends(get_hub)) -> list[dict[str, Any]]:
    return hub.list_connectors(user_id, is_admin(request))


@router.get("/me")
def me(request: Request) -> dict[str, bool]:
    """Quyền của người đang dùng trên giao diện Kết nối."""
    return {"is_admin": is_admin(request)}


@router.post("/connectors/discover")
def discover(body: DiscoverIn, hub=Depends(get_hub)) -> dict[str, Any]:
    from app.services.agent.mcp.oauth.discovery import check_url, discover as run

    url = _call(check_url, body.url, bool(hub.cfg.get("allow_http_localhost", True)))
    d = _call(run, url, hub.http(), client_name=hub.cfg.get("client_name", "MEP Project Assistant"))
    return {"auth_type": d.auth_type, "status_code": d.status_code, "resource": d.resource, "issuer": d.issuer,
            "scopes": d.scopes, "has_prm": d.has_prm, "registration": d.registration, "notes": d.notes}


@router.post("/connectors", status_code=201)
def create_connector(body: ConnectorIn, request: Request, hub=Depends(get_hub)) -> dict[str, Any]:
    admin = is_admin(request)
    if body.shared and not admin:
        raise HTTPException(403, "Chỉ quản trị viên được thêm connector dùng chung cho tổ chức")
    data = body.model_dump(exclude={"user_id", "shared"})
    data["headers"] = _headers(body.headers)
    row = _call(hub.registry.create, data, body.user_id, body.shared)
    return hub.connector_view(row, {}, body.user_id, admin)


@router.patch("/connectors/{connector_id}")
def update_connector(connector_id: str, body: ConnectorPatch, request: Request, user_id: str = Query(...),
                     hub=Depends(get_hub)) -> dict[str, Any]:
    admin = is_admin(request)
    old = _managed_row(hub, connector_id, user_id, admin)
    if body.shared is not None and body.shared != bool(old.get("shared")):
        if not admin:
            raise HTTPException(403, "Chỉ quản trị viên được đưa connector lên / gỡ khỏi dùng chung")
        if not body.shared and old.get("created_by") != user_id:
            # gỡ khỏi dùng chung → thành connector cá nhân của người tạo; admin khác sẽ không thấy nữa
            raise HTTPException(400, "Chỉ người tạo được chuyển connector này về cá nhân – hoặc xóa connector")
    data = body.model_dump(exclude_none=True)
    if body.headers is not None:
        data["headers"] = _headers(body.headers)
    row, _ = _call(hub.registry.update, connector_id, data)
    if old.get("shared") and not row.get("shared"):
        # về cá nhân: token của người khác không còn dùng được – xóa cho sạch
        hub.registry.oauth_store.delete_connector_tokens(connector_id)
    hub.invalidate(connector_id)
    return hub.connector_view(row, hub.tokens.store.token_statuses(user_id), user_id, admin)


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: str, request: Request, user_id: str = Query(...),
                     hub=Depends(get_hub)) -> Response:
    _managed_row(hub, connector_id, user_id, is_admin(request))
    _call(hub.registry.delete, connector_id)
    hub.invalidate(connector_id)
    return Response(status_code=204)


# ------------------------------------------------------------------ OAuth từng user

@router.post("/connectors/{connector_id}/connect")
def connect(connector_id: str, response: Response, user_id: str = Query(...), hub=Depends(get_hub)) -> dict[str, str]:
    _db_row(hub, connector_id, user_id)
    url = _call(hub.oauth.connect, connector_id, user_id)
    # Gắn trình duyệt khởi tạo với callback (cookie cùng origin frontend, SameSite=Lax gửi kèm điều hướng về)
    response.set_cookie(USER_COOKIE, user_id, max_age=int(hub.cfg.get("state_ttl_s", 600)), path="/mcp/oauth",
                        httponly=True, samesite="lax")
    return {"authorize_url": url}


def _callback_page(result: dict[str, Any], public_base_url: str) -> str:
    u = urlparse(public_base_url)
    origin = f"{u.scheme}://{u.netloc}"
    msg = {"type": "mcp-oauth", **result}
    js = lambda v: json.dumps(v, ensure_ascii=False).replace("<", "\\u003c")   # noqa: E731
    ok = result.get("status") == "connected"
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Kết nối MCP</title></head>
<body style="font-family:system-ui,sans-serif;padding:24px">
<h3>{'Đã kết nối' if ok else 'Kết nối chưa thành công'}</h3><p>{html.escape(str(result.get('message') or ''))}</p>
<p>Có thể đóng cửa sổ này.</p>
<script>
const msg = {js(msg)};
if (window.opener) {{ window.opener.postMessage(msg, {js(origin)}); window.close(); }}
else {{ setTimeout(() => location.replace("/?view=connectors"), 1500); }}
</script></body></html>"""


@router.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                   hub=Depends(get_hub)) -> HTMLResponse:
    result = hub.oauth.callback(code=code, state=state, error=error, user_hint=request.cookies.get(USER_COOKIE))
    resp = HTMLResponse(_callback_page(result, str(hub.cfg.get("public_base_url") or "http://localhost:5173")))
    resp.delete_cookie(USER_COOKIE, path="/mcp/oauth")
    return resp


@router.post("/connectors/{connector_id}/disconnect")
def disconnect(connector_id: str, user_id: str = Query(...), hub=Depends(get_hub)) -> dict[str, str]:
    _db_row(hub, connector_id, user_id)
    return {"message": _call(hub.oauth.disconnect, connector_id, user_id)}


# ------------------------------------------------------------------ tool

def _yaml_tools(runtime: AgentRuntime, connector_id: str) -> list[dict[str, Any]]:
    name = connector_id.split(":", 1)[1]
    return [{"name": (t.metadata or {}).get("mcp_tool", t.name), "full_name": t.name, "title": None,
             "description": t.description, "default_access": t.metadata.get("access"), "override": None,
             "access": t.metadata.get("access"), "annotations": {}, "readonly": True}
            for t in (runtime.sources.yaml_tools.get(name, []) if runtime.sources else [])]


@router.get("/connectors/{connector_id}/tools")
def list_tools(connector_id: str, user_id: str = Query(...), hub=Depends(get_hub),
               runtime: AgentRuntime = Depends(get_runtime)) -> list[dict[str, Any]]:
    if hub.is_yaml(connector_id):
        return _yaml_tools(runtime, connector_id)
    row = _db_row(hub, connector_id, user_id)
    if row["auth_type"] == "oauth" and hub.tokens.status(user_id, connector_id) is None:
        raise HTTPException(409, "Chưa kết nối – bấm Kết nối để xem danh sách tool")
    try:
        return hub.tools.views(row, user_id)
    except Exception as e:  # noqa: BLE001
        status, msg = hub.tools.explain(row, e)
        raise HTTPException(502, {"code": status, "message": msg}) from None


@router.post("/connectors/{connector_id}/describe")
def describe(connector_id: str, request: Request, user_id: str = Query(...), hub=Depends(get_hub)) -> dict[str, str]:
    """Gợi ý mô tả cho danh mục nguồn của agent (không lưu – người dùng xem lại rồi bấm Lưu)."""
    row = _managed_row(hub, connector_id, user_id, is_admin(request))
    if row["auth_type"] == "oauth" and hub.tokens.status(user_id, connector_id) is None:
        raise HTTPException(409, "Chưa kết nối – bấm Kết nối trước để server trả danh sách tool")
    try:
        return {"description": hub.suggest_description(row, user_id)}
    except Exception as e:  # noqa: BLE001
        status, msg = hub.tools.explain(row, e)
        raise HTTPException(502, {"code": status, "message": msg}) from None


@router.put("/connectors/{connector_id}/tools/{tool_name}")
def set_tool_access(connector_id: str, tool_name: str, body: AccessIn, request: Request,
                    user_id: str = Query(...), hub=Depends(get_hub)) -> dict[str, Any]:
    _managed_row(hub, connector_id, user_id, is_admin(request))
    hub.registry.store.set_override(connector_id, tool_name, body.access)
    return {"tool": tool_name, "override": body.access}


@router.post("/connectors/{connector_id}/test")
def test_connector(connector_id: str, user_id: str = Query(...), hub=Depends(get_hub)) -> dict[str, Any]:
    row = _db_row(hub, connector_id, user_id)
    if row["auth_type"] == "oauth" and hub.tokens.status(user_id, connector_id) is None:
        raise HTTPException(409, "Chưa kết nối – bấm Kết nối trước")
    return _call(hub.tools.probe, row, user_id)
