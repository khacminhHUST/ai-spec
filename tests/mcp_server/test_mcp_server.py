"""Unit – MCP server "Thông tin dự án" cho Claude (docs/mcp-server.md): xác thực token user-management (RS256, iss,
aud, exp), metadata RFC 9728, quyền đọc ở tầng tool, tra cứu N1–N6 trên seed S5, không ảnh hưởng API cũ."""
import datetime as dt
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.trusted_proxy import TrustedProxyAuth
from app.main import create_app
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.mcp_server.auth import key_id
from app.services.mcp_server.config import load_config
from tests.agent.fakes import ScriptedModel
from tests.query.fakes import S5, InMemoryRepo, make_deps, router_json, scripted_llm

pytestmark = pytest.mark.unit

RESOURCE = "https://trithuc.hawee.com.vn/thongtinduan/mcp"
ISSUER = "https://user-management.hawee.com.vn"
EMAIL = "ky.su@hawee.com.vn"
BEND = router_json(q="Bán kính uốn cong tối thiểu của cáp tiết diện lớn hơn 25mm2",
                   queries_vi=["bán kính uốn cong tối thiểu cáp tiết diện lớn hơn 25mm2"],
                   queries_en=["minimum bend radius cable larger 25mm2"], exact_keywords=["25mm2"])
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def keys():
    def make():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = k.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        return k, pem
    return make(), make()


def _token(priv, pem, *, aud=(RESOURCE,), iss=ISSUER, email=EMAIL, perms=None, exp_min=60):
    now = dt.datetime.now(dt.timezone.utc)
    claims = {"iss": iss, "sub": "42", "email": email, "aud": list(aud), "iat": now,
              "exp": now + dt.timedelta(minutes=exp_min), "jti": "x",
              "permissions": {RESOURCE: ["read"]} if perms is None else perms}
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": key_id(pem)})


@pytest.fixture
def client(mock_settings, keys, monkeypatch):
    (priv, pem), _ = keys
    monkeypatch.delenv("MCP_SERVER_RESOURCE_URL", raising=False)

    def make(trusted_proxy=False, **over):
        cfg = load_config({"resource_url": RESOURCE, "public_key_pem": pem, **over})
        repo = InMemoryRepo(__import__("tests.fixtures.query_seed.seed", fromlist=["load_seed"]).load_seed(),
                            users={EMAIL: [S5]})
        deps = make_deps(mock_settings, llm=scripted_llm(BEND), repo=repo)
        rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800),
                           model=ScriptedModel(script=["ok"], calls=[]), yaml_tools=[], mcp=None)
        app = create_app(deps, InMemorySaver(), rt, mcp_server_cfg=cfg)
        if trusted_proxy:
            app.add_middleware(TrustedProxyAuth)
        return TestClient(app), cfg
    return make


def _rpc(c, method, params=None, token=None, path="/kb-mcp"):
    headers = {**MCP_HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return c.post(path, headers=headers, json=body)


def _init(c, token):
    return _rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}}, token)


def _call(c, token, name, args=None):
    r = _rpc(c, "tools/call", {"name": name, "arguments": args or {}}, token)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return res, (json.loads(res["content"][0]["text"]) if not res.get("isError") else res["content"][0]["text"])


def test_prm_and_401_point_claude_to_user_management(client):
    c, cfg = client()
    with c:
        assert cfg.prm_path == "/.well-known/oauth-protected-resource/thongtinduan/mcp"
        prm = c.get(cfg.prm_path).json()
        assert prm["resource"] == RESOURCE and prm["authorization_servers"] == [ISSUER]   # issuer trần, không "/"
        r = _init(c, None)
        assert r.status_code == 401
        assert f'resource_metadata="{cfg.prm_url}"' in r.headers["www-authenticate"]
        assert cfg.prm_url == "https://trithuc.hawee.com.vn/.well-known/oauth-protected-resource/thongtinduan/mcp"


def test_rejects_bad_tokens(client, keys):
    (priv, pem), (other_priv, other_pem) = keys
    c, _ = client()
    with c:
        assert _init(c, _token(priv, pem)).status_code == 200
        assert _init(c, _token(priv, pem, aud=("ai-thongtinduan",))).status_code == 200      # nhánh scope (tên)
        bad = [_token(other_priv, pem),                     # chữ ký khóa khác (kid giả mạo)
               _token(priv, pem, iss="https://evil.example.com"),
               _token(priv, pem, aud=("ai-trithuc",)),      # token cho server khác
               _token(priv, pem, exp_min=-10),
               _token(priv, pem, email=""),
               jwt.encode({"iss": ISSUER, "sub": "42", "aud": [RESOURCE], "email": EMAIL, "iat": 0, "exp": 2**31},
                          "k" * 32, algorithm="HS256")]     # đổi thuật toán
        for t in bad:
            assert _init(c, t).status_code == 401


def test_tools_list_and_read_only(client, keys):
    (priv, pem), _ = keys
    c, _ = client()
    with c:
        tools = _rpc(c, "tools/list", token=_token(priv, pem)).json()["result"]["tools"]
        assert {t["name"] for t in tools} == {"search_project_docs", "list_projects"}
        assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_search_and_list_projects(client, keys):
    (priv, pem), _ = keys
    c, _ = client()
    with c:
        res, out = _call(c, _token(priv, pem), "search_project_docs", {"question": "Bán kính uốn cáp > 25mm2?"})
        assert not res.get("isError") and out["status"] in ("OK", "PARTIAL")
        assert out["blocks"][0]["n"] == 1 and "uốn cong" in out["blocks"][0]["section"]
        assert [x["n"] for x in out["citations"]] == [b["n"] for b in out["blocks"]]
        _, projects = _call(c, _token(priv, pem), "list_projects")
        assert [p["project_id"] for p in projects["projects"]] == [S5]


def test_missing_read_permission_is_tool_error(client, keys):
    (priv, pem), _ = keys
    c, _ = client()
    with c:
        tok = _token(priv, pem, perms={RESOURCE: []})
        assert _init(c, tok).status_code == 200                       # kết nối vẫn được
        res, msg = _call(c, tok, "search_project_docs", {"question": "x"})
        assert res["isError"] and "chưa được cấp quyền 'read'" in msg
    c2, _ = client(require_permission="")                              # tắt yêu cầu quyền: chỉ cần token hợp lệ
    with c2:
        res, _ = _call(c2, _token(priv, pem, perms={}), "list_projects")
        assert not res.get("isError")


def test_trusted_proxy_does_not_block_mcp_and_old_api_unchanged(client, keys):
    (priv, pem), _ = keys
    c, cfg = client(trusted_proxy=True)
    with c:
        assert c.get(cfg.prm_path).status_code == 200                 # không token Tri thức vẫn đọc được metadata
        assert _init(c, None).status_code == 401 and "resource_metadata" in _init(c, None).headers["www-authenticate"]
        assert _init(c, _token(priv, pem)).status_code == 200
        assert c.get("/conversations", params={"user_id": "x"}).status_code == 401   # API cũ vẫn cần token Tri thức


def test_disabled(client):
    c, _ = client(enabled=False)
    with c:
        assert c.post("/kb-mcp", json={}).status_code == 404
