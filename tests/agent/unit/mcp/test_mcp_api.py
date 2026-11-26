"""Unit – API /mcp: CRUD connector, discover, connect → callback (HTML postMessage), mô tả nguồn, tool + ghi đè,
yaml chỉ đọc, thiếu MEP_SECRET_KEY (TestClient, store in-memory, MockTransport)."""
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from mcp.types import Tool, ToolAnnotations

from app.main import create_app
from app.providers.llm import MockChatModel
from app.services.agent.mcp.hub import McpHub
from app.services.agent.mcp.oauth.crypto import SecretBox
from app.services.agent.mcp.oauth.errors import SecretKeyMissing
from app.services.agent.mcp.session import ServerListing
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from tests.agent.fakes import ScriptedModel
from tests.agent.unit.mcp.fakes import MCP_URL, SECRET, FakeAS, MemoryStore, mock_http, prm_server
from tests.agent.unit.mcp.test_mcp_connectors import CFG
from tests.query.fakes import make_deps

pytestmark = pytest.mark.unit
TOOLS = [Tool(name="list_items", description="Liệt kê", inputSchema={"type": "object"},
              annotations=ToolAnnotations(readOnlyHint=True)),
         Tool(name="create_item", description="Tạo", inputSchema={"type": "object"})]


@pytest.fixture
def api(mock_settings, monkeypatch):
    def make(box=lambda: SecretBox(SECRET)):
        fa, store = FakeAS(), MemoryStore()
        open_srv = lambda r: httpx.Response(200, json={}) if r.url.host == "pub.example.com" else None   # noqa: E731
        hub = McpHub(CFG, store, store, box=box, http=mock_http(prm_server(), fa.handle, open_srv))
        monkeypatch.setattr(hub.tools, "mcp_tools", lambda row, user, fresh=False: TOOLS)
        monkeypatch.setattr(hub.tools, "listing", lambda row, user: ServerListing(tools=TOOLS))
        deps = make_deps(mock_settings, llm=MockChatModel(responses=["x"], calls=[]))
        rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800),
                           model=ScriptedModel(script=["ok"], calls=[]), yaml_tools=[], mcp=hub)
        return TestClient(create_app(deps, InMemorySaver(), rt)), hub, fa, store
    return make


def test_discover_create_connect_callback_tools(api):
    c, hub, fa, store = api()
    with c:
        d = c.post("/mcp/connectors/discover", json={"url": MCP_URL}).json()
        assert d["auth_type"] == "oauth" and d["registration"] and d["resource"] == MCP_URL
        assert c.post("/mcp/connectors/discover", json={"url": "https://pub.example.com/mcp"}).json()["auth_type"] == "none"
        assert c.post("/mcp/connectors/discover", json={"url": "http://x.vn/mcp"}).status_code == 400
        r = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth", "user_id": "u1"})
        assert r.status_code == 201, r.text
        cid = r.json()["connector_id"]
        lst = c.get("/mcp/connectors", params={"user_id": "u1"}).json()
        assert next(x for x in lst if x["name"] == "kho")["status"] == "not_connected"
        assert c.get(f"/mcp/connectors/{cid}/tools", params={"user_id": "u1"}).status_code == 409
        r = c.post(f"/mcp/connectors/{cid}/connect", params={"user_id": "u1"})
        assert r.status_code == 200 and "mep_oauth_uid" in r.headers.get("set-cookie", "")
        p = httpx.URL(r.json()["authorize_url"]).params
        code = fa.issue_code("u1", p["client_id"], p["redirect_uri"], p["code_challenge"])
        page = c.get("/mcp/oauth/callback", params={"code": code, "state": p["state"]})
        assert page.status_code == 200 and "postMessage" in page.text
        msg = json.loads(re.search(r"const msg = (\{.*?\});", page.text).group(1))
        assert msg == {"type": "mcp-oauth", "connector_id": cid, "status": "connected", "message": "Đã kết nối"}
        assert "http://localhost:5173" in page.text
        lst = c.get("/mcp/connectors", params={"user_id": "u1"}).json()
        kho = next(x for x in lst if x["name"] == "kho")
        assert kho["status"] == "connected"
        assert kho["description"] == "Công cụ: list_items, create_item"          # tự điền khi kết nối lần đầu
        assert c.post(f"/mcp/connectors/{cid}/describe", params={"user_id": "u1"}).json() == {
            "description": "Công cụ: list_items, create_item"}
        assert c.post(f"/mcp/connectors/{cid}/describe", params={"user_id": "u2"}).status_code == 404   # của u1
        r = c.patch(f"/mcp/connectors/{cid}", json={"description": "Kho vật tư:  tồn kho, nhập xuất"}, params={"user_id": "u1"})
        assert r.json()["description"] == "Kho vật tư: tồn kho, nhập xuất" and r.json()["status"] == "connected"
        assert all(x["name"] != "kho" for x in c.get("/mcp/connectors", params={"user_id": "u2"}).json())
        tools = c.get(f"/mcp/connectors/{cid}/tools", params={"user_id": "u1"}).json()
        assert {t["name"]: t["access"] for t in tools} == {"list_items": "read", "create_item": "write"}
        assert c.put(f"/mcp/connectors/{cid}/tools/create_item", json={"access": "disabled"}, params={"user_id": "u1"}).status_code == 200
        tools = c.get(f"/mcp/connectors/{cid}/tools", params={"user_id": "u1"}).json()
        assert next(t for t in tools if t["name"] == "create_item")["access"] == "disabled"
        assert {t.name for t in hub.source_tools("u1", "kho")} == {"kho_list_items"}
        assert c.post(f"/mcp/connectors/{cid}/test", params={"user_id": "u1"}).json()["ok"]
        r = c.post(f"/mcp/connectors/{cid}/disconnect", params={"user_id": "u1"})
        assert "60 phút" in r.json()["message"]
        assert c.patch(f"/mcp/connectors/{cid}", json={"enabled": False}, params={"user_id": "u1"}).json()["enabled"] is False
        assert c.delete(f"/mcp/connectors/{cid}", params={"user_id": "u1"}).status_code == 204
        assert c.delete(f"/mcp/connectors/{cid}", params={"user_id": "u1"}).status_code == 404


def test_callback_errors_and_yaml_readonly(api):
    c, hub, fa, _ = api()
    with c:
        page = c.get("/mcp/oauth/callback", params={"state": "khong-co", "code": "x"})
        assert page.status_code == 200 and '"status": "error"' in page.text
        cid = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth", "user_id": "u1"}).json()["connector_id"]
        p = httpx.URL(c.post(f"/mcp/connectors/{cid}/connect", params={"user_id": "u1"}).json()["authorize_url"]).params
        page = c.get("/mcp/oauth/callback", params={"error": "invalid_target", "state": p["state"]})
        assert "chưa được quản trị" in page.text
        y = next(x for x in c.get("/mcp/connectors", params={"user_id": "u1"}).json() if x["source"] == "yaml")
        assert c.patch(f"/mcp/connectors/{y['connector_id']}", json={"enabled": False}, params={"user_id": "u1"}).status_code == 400
        assert c.post(f"/mcp/connectors/{y['connector_id']}/connect", params={"user_id": "u1"}).status_code == 400
        assert c.get(f"/mcp/connectors/{y['connector_id']}/tools", params={"user_id": "u1"}).json() == []
        r = c.post("/mcp/connectors", json={"name": "h1", "url": MCP_URL, "auth_type": "headers",
                                            "headers": [{"name": "Authorization", "value": "Bearer abc"}], "user_id": "u1"})
        assert r.status_code == 201 and r.json()["header_names"] == ["Authorization"] and "abc" not in r.text
        assert c.post("/mcp/connectors", json={"name": "Bad Name", "url": MCP_URL, "auth_type": "none", "user_id": "u1"}).status_code == 400
        assert c.post("/mcp/connectors", json={"name": "pub2", "url": MCP_URL, "auth_type": "none"}).status_code == 422


def test_missing_secret_key_is_clear_error(api):
    def no_key():
        raise SecretKeyMissing()

    c, *_ = api(box=no_key)
    with c:
        r = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth", "user_id": "u1"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "secret_key_missing"
        assert c.post("/mcp/connectors", json={"name": "pub", "url": "https://pub.example.com/mcp",
                                               "auth_type": "none", "user_id": "u1"}).status_code == 201


def test_connectors_are_private_per_user(api):
    """Mỗi người chỉ thấy / sửa / kết nối / đặt quyền connector của mình; tên chỉ cần riêng trong phạm vi 1 người."""
    c, hub, fa, store = api()
    u1, u2 = {"user_id": "a@hawee.vn"}, {"user_id": "b@hawee.vn"}
    with c:
        a = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth", **u1}).json()
        b = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth", **u2})
        assert b.status_code == 201, b.text                                       # cùng tên, khác người: được
        b = b.json()
        assert c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth", **u1}).status_code == 400
        assert [x["connector_id"] for x in c.get("/mcp/connectors", params=u1).json() if x["source"] == "db"] == [a["connector_id"]]
        assert [x["connector_id"] for x in c.get("/mcp/connectors", params=u2).json() if x["source"] == "db"] == [b["connector_id"]]
        assert store.connectors[a["connector_id"]]["created_by"] == "a@hawee.vn"

        aid = a["connector_id"]
        for method, path, kw in [("patch", "", {"json": {"enabled": False}}), ("delete", "", {}),
                                 ("post", "/connect", {}), ("post", "/disconnect", {}), ("post", "/test", {}),
                                 ("post", "/describe", {}), ("get", "/tools", {}),
                                 ("put", "/tools/create_item", {"json": {"access": "write"}})]:
            r = getattr(c, method)(f"/mcp/connectors/{aid}{path}", params=u2, **kw)
            assert r.status_code == 404, (method, path, r.status_code)
        assert store.connectors[aid]["enabled"] is True and store.get_overrides(aid) == {}

        # Quyền tool của B không ảnh hưởng A (2 connector riêng)
        assert c.put(f"/mcp/connectors/{b['connector_id']}/tools/list_items", json={"access": "disabled"}, params=u2).status_code == 200
        assert store.get_overrides(aid) == {}

        # Kết nối OAuth của A: B không dùng được nguồn 'kho' của A trong agent
        p = httpx.URL(c.post(f"/mcp/connectors/{aid}/connect", params=u1).json()["authorize_url"]).params
        code = fa.issue_code("a@hawee.vn", p["client_id"], p["redirect_uri"], p["code_challenge"])
        assert '"status": "connected"' in c.get("/mcp/oauth/callback", params={"code": code, "state": p["state"]}).text
        assert {t.name for t in hub.source_tools("a@hawee.vn", "kho")} == {"kho_list_items", "kho_create_item"}
        assert [e["status"] for e in hub.catalog("b@hawee.vn") if e["name"] == "kho"] == ["not_connected"]  # 'kho' của B
        assert ("b@hawee.vn", aid) not in store.tokens


def _tri_thuc_token(email: str, role: str) -> str:
    """Token web Tri thức giả (TrustedProxyAuth chỉ đọc payload, không kiểm chữ ký)."""
    import base64
    import time

    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")   # noqa: E731
    return f"{b({'alg': 'HS256'})}.{b({'email': email, 'role': role, 'type': 'access', 'exp': time.time() + 600})}.x"


def test_shared_connectors_admin_manages_everyone_connects(api):
    """Connector dùng chung: ADMIN thêm/sửa/xóa/đặt quyền tool; người khác thấy, tự kết nối (token riêng), không sửa được."""
    from app.api.trusted_proxy import TrustedProxyAuth

    c, hub, fa, store = api()
    c.app.add_middleware(TrustedProxyAuth)
    adm = {"Authorization": "Bearer " + _tri_thuc_token("admin@hawee.vn", "ADMIN")}
    usr = {"Authorization": "Bearer " + _tri_thuc_token("b@hawee.vn", "USER")}
    q = {"user_id": "x"}                                   # bị TrustedProxyAuth ghi đè bằng email trong token
    with c:
        assert c.get("/mcp/me", headers=adm).json() == {"is_admin": True}
        assert c.get("/mcp/me", headers=usr).json() == {"is_admin": False}
        body = {"name": "revit", "url": MCP_URL, "auth_type": "oauth", "shared": True, "user_id": "x"}
        assert c.post("/mcp/connectors", json=body, headers=usr).status_code == 403
        r = c.post("/mcp/connectors", json=body, headers=adm)
        assert r.status_code == 201, r.text
        sid = r.json()["connector_id"]
        assert r.json()["shared"] is True and r.json()["can_manage"] is True

        # Người thường thấy connector dùng chung, chưa kết nối, không quản lý được
        v = next(x for x in c.get("/mcp/connectors", params=q, headers=usr).json() if x["name"] == "revit")
        assert v["shared"] and not v["can_manage"] and v["status"] == "not_connected"
        for method, path, kw in [("patch", "", {"json": {"enabled": False}}), ("delete", "", {}),
                                 ("post", "/describe", {}),
                                 ("put", "/tools/create_item", {"json": {"access": "write"}})]:
            r = getattr(c, method)(f"/mcp/connectors/{sid}{path}", params=q, headers=usr, **kw)
            assert r.status_code == 403, (method, path, r.status_code)
        # Tên dùng chung không được trùng với connector cá nhân mới
        assert c.post("/mcp/connectors", json={**body, "shared": False}, headers=usr).status_code == 400

        # Người thường tự kết nối → token của riêng họ, agent dùng được nguồn
        p = httpx.URL(c.post(f"/mcp/connectors/{sid}/connect", params=q, headers=usr).json()["authorize_url"]).params
        code = fa.issue_code("b@hawee.vn", p["client_id"], p["redirect_uri"], p["code_challenge"])
        assert '"status": "connected"' in c.get("/mcp/oauth/callback", params={"code": code, "state": p["state"]}).text
        assert ("b@hawee.vn", sid) in store.tokens and ("admin@hawee.vn", sid) not in store.tokens
        assert {t.name for t in hub.source_tools("b@hawee.vn", "revit")} == {"revit_list_items", "revit_create_item"}
        assert [e["status"] for e in hub.catalog("admin@hawee.vn") if e["name"] == "revit"] == ["not_connected"]
        assert c.get(f"/mcp/connectors/{sid}/tools", params=q, headers=usr).status_code == 200

        # Admin đặt quyền tool → áp cho mọi người
        assert c.put(f"/mcp/connectors/{sid}/tools/create_item", json={"access": "disabled"}, params=q,
                     headers=adm).status_code == 200
        hub.invalidate(sid)
        assert {t.name for t in hub.source_tools("b@hawee.vn", "revit")} == {"revit_list_items"}

        # Admin đưa connector cá nhân lên dùng chung; người thường không được
        own = c.post("/mcp/connectors", json={"name": "pub", "url": "https://pub.example.com/mcp", "auth_type": "none",
                                              "user_id": "x"}, headers=usr).json()
        assert c.patch(f"/mcp/connectors/{own['connector_id']}", json={"shared": True}, params=q,
                       headers=usr).status_code == 403
        mine = c.post("/mcp/connectors", json={"name": "pub2", "url": "https://pub.example.com/mcp", "auth_type": "none",
                                               "user_id": "x"}, headers=adm).json()
        assert not mine["shared"]
        r = c.patch(f"/mcp/connectors/{mine['connector_id']}", json={"shared": True}, params=q, headers=adm)
        assert r.status_code == 200 and r.json()["shared"] is True
        assert any(x["name"] == "pub2" for x in c.get("/mcp/connectors", params=q, headers=usr).json())

        # Admin xóa → mất với mọi người (kèm token)
        assert c.delete(f"/mcp/connectors/{sid}", params=q, headers=adm).status_code == 204
        assert ("b@hawee.vn", sid) not in store.tokens
        assert all(x["name"] != "revit" for x in c.get("/mcp/connectors", params=q, headers=usr).json())


def test_shared_name_wins_over_personal_in_agent(api):
    """Người dùng có connector cá nhân trùng tên với connector dùng chung (tạo trước): agent dùng connector dùng chung."""
    c, hub, fa, store = api()
    with c:
        mine = c.post("/mcp/connectors", json={"name": "kho", "url": "https://pub.example.com/mcp", "auth_type": "none",
                                               "user_id": "b"}).json()
        org = c.post("/mcp/connectors", json={"name": "kho", "url": "https://pub.example.com/mcp", "auth_type": "none",
                                              "display_name": "Kho tổ chức", "shared": True, "user_id": "admin"})
        assert org.status_code == 201, org.text
        assert [e["label"] for e in hub.catalog("b") if e["name"] == "kho"] == ["Kho tổ chức"]
        ids = [x["connector_id"] for x in c.get("/mcp/connectors", params={"user_id": "b"}).json() if x["source"] == "db"]
        assert ids == [org.json()["connector_id"], mine["connector_id"]]        # giao diện vẫn hiện cả 2
