"""Unit – API /mcp: CRUD connector, discover, connect → callback (HTML postMessage), tool + ghi đè, yaml chỉ đọc,
thiếu MEP_SECRET_KEY (TestClient, store in-memory, MockTransport)."""
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
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.oauth.crypto import SecretBox
from app.services.oauth.errors import SecretKeyMissing
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
        deps = make_deps(mock_settings, llm=MockChatModel(responses=['{"route": "agent", "reason": "t"}'], calls=[]))
        rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800),
                           model=ScriptedModel(script=["ok"], calls=[]), mcp_tools=[], mcp=hub)
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
        assert next(x for x in lst if x["name"] == "kho")["status"] == "connected"
        assert next(x for x in c.get("/mcp/connectors", params={"user_id": "u2"}).json()
                    if x["name"] == "kho")["status"] == "not_connected"
        tools = c.get(f"/mcp/connectors/{cid}/tools", params={"user_id": "u1"}).json()
        assert {t["name"]: t["access"] for t in tools} == {"list_items": "read", "create_item": "write"}
        assert c.put(f"/mcp/connectors/{cid}/tools/create_item", json={"access": "disabled"}).status_code == 200
        tools = c.get(f"/mcp/connectors/{cid}/tools", params={"user_id": "u1"}).json()
        assert next(t for t in tools if t["name"] == "create_item")["access"] == "disabled"
        assert {t.name for t in hub.turn_tools("u1")} == {"kho_list_items"}
        assert c.post(f"/mcp/connectors/{cid}/test", params={"user_id": "u1"}).json()["ok"]
        r = c.post(f"/mcp/connectors/{cid}/disconnect", params={"user_id": "u1"})
        assert "60 phút" in r.json()["message"]
        assert c.patch(f"/mcp/connectors/{cid}", json={"enabled": False}).json()["enabled"] is False
        assert c.delete(f"/mcp/connectors/{cid}").status_code == 204
        assert c.delete(f"/mcp/connectors/{cid}").status_code == 404


def test_callback_errors_and_yaml_readonly(api):
    c, hub, fa, _ = api()
    with c:
        page = c.get("/mcp/oauth/callback", params={"state": "khong-co", "code": "x"})
        assert page.status_code == 200 and '"status": "error"' in page.text
        cid = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth"}).json()["connector_id"]
        p = httpx.URL(c.post(f"/mcp/connectors/{cid}/connect", params={"user_id": "u1"}).json()["authorize_url"]).params
        page = c.get("/mcp/oauth/callback", params={"error": "invalid_target", "state": p["state"]})
        assert "chưa được quản trị" in page.text
        y = next(x for x in c.get("/mcp/connectors", params={"user_id": "u1"}).json() if x["source"] == "yaml")
        assert c.patch(f"/mcp/connectors/{y['connector_id']}", json={"enabled": False}).status_code == 400
        assert c.post(f"/mcp/connectors/{y['connector_id']}/connect", params={"user_id": "u1"}).status_code == 400
        assert c.get(f"/mcp/connectors/{y['connector_id']}/tools", params={"user_id": "u1"}).json() == []
        r = c.post("/mcp/connectors", json={"name": "h1", "url": MCP_URL, "auth_type": "headers",
                                            "headers": [{"name": "Authorization", "value": "Bearer abc"}]})
        assert r.status_code == 201 and r.json()["header_names"] == ["Authorization"] and "abc" not in r.text
        assert c.post("/mcp/connectors", json={"name": "Bad Name", "url": MCP_URL, "auth_type": "none"}).status_code == 400


def test_missing_secret_key_is_clear_error(api):
    def no_key():
        raise SecretKeyMissing()

    c, *_ = api(box=no_key)
    with c:
        r = c.post("/mcp/connectors", json={"name": "kho", "url": MCP_URL, "auth_type": "oauth"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "secret_key_missing"
        assert c.post("/mcp/connectors", json={"name": "pub", "url": "https://pub.example.com/mcp",
                                               "auth_type": "none"}).status_code == 201
