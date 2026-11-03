"""Integration – MCP connector với A giả + MCP server giả (uvicorn thật) + Postgres thật (namespace tạm).

Kịch bản docs/mcp-connectors.md mục 8: chỉ bằng URL (khám phá → DCR → đồng ý → token → tool), tool ghi cần xác nhận,
tự refresh, A thu hồi → needs_reauth, không quyền → forbidden, connector none/headers, hai user token riêng,
hai refresh đồng thời chỉ gọi A 1 lần, token không lọt log.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.db.client import pg_pool
from app.main import create_app
from app.providers.llm import MockChatModel
from app.services.agent.mcp.catalog import SourceUnavailable
from app.services.agent.mcp.hub import McpHub
from app.services.agent.mcp.oauth.crypto import SecretBox
from app.services.agent.mcp.oauth.store import PgOAuthStore
from app.services.agent.mcp.store import PgConnectorStore
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.agent.tools.source_tool import NAME as OPEN
from tests.agent.fakes import ScriptedModel, tool_call
from tests.fixtures.oauth_fake.fake_a import FakeServerA
from tests.fixtures.oauth_fake.mcp_servers import oauth_server, open_server, require_header
from tests.fixtures.oauth_fake.serve import Served, reserve
from tests.query.fakes import make_deps

pytestmark = pytest.mark.integration
BOX = SecretBox("integration-secret-key-0123456789")
API_KEY = "k-secret-123"
CFG = {"public_base_url": "http://localhost:5173", "client_name": "MEP Project Assistant", "tool_cache_ttl_s": 600,
       "refresh_skew_s": 60, "http_timeout_s": 15, "allow_http_localhost": True, "state_ttl_s": 600, "servers": {}}


@pytest.fixture(scope="module")
def servers():
    socks = [reserve() for _ in range(4)]
    (sa, pa), (sm, pm), (so, po), (sh, ph) = socks
    issuer, resource = f"http://127.0.0.1:{pa}", f"http://127.0.0.1:{pm}/mcp"
    users = {u: {"ai-kho": ["read", "write"]} for u in ("alice", "carol", "dave", "erin")}
    users.update(bob={"ai-kho": ["read"]}, nobody={})
    a = FakeServerA(issuer, {resource: "ai-kho"}, users)
    created: list = []
    running = [Served(a.app, sa), Served(oauth_server(issuer, resource, a.public_pem, created).streamable_http_app(), sm),
               Served(open_server().streamable_http_app(), so),
               Served(require_header(open_server("hdr").streamable_http_app(), "X-API-Key", API_KEY), sh)]
    yield SimpleNamespace(a=a, issuer=issuer, resource=resource, open_url=f"http://127.0.0.1:{po}/mcp",
                          hdr_url=f"http://127.0.0.1:{ph}/mcp", created=created)
    for s in running:
        s.stop()


@pytest.fixture
def env(test_ns, servers, mock_settings):
    pool = pg_pool(test_ns)
    hub = McpHub(CFG, PgOAuthStore(pool), PgConnectorStore(pool), box=lambda: BOX)

    def client(script):
        deps = make_deps(mock_settings, llm=MockChatModel(responses=["x"], calls=[]))
        model = ScriptedModel(script=script, calls=[])
        rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800), model=model, yaml_tools=[],
                           mcp=hub)
        return TestClient(create_app(deps, InMemorySaver(), rt)), model

    yield SimpleNamespace(hub=hub, pool=pool, client=client, w=servers)
    pool.close()


# ------------------------------------------------------------------ tiện ích

def _sse(text):
    out, ev, data = [], None, []
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line.strip() and ev:
            d = "\n".join(data)
            try:
                d = json.loads(d)
            except ValueError:
                pass
            out.append((ev, d))
            ev, data = None, []
    return out


def _chat(c, user, question="làm việc với kho"):
    r = c.post("/chat", json={"user_id": user, "question": question})
    assert r.status_code == 200, r.text
    return _sse(r.text)


def _first(events, kind):
    """Sự kiện đầu tiên loại `kind` (tool_end: bỏ qua bước mở nguồn)."""
    return next(d for e, d in events if e == kind and not (kind == "tool_end" and d["tool"] == OPEN))


def _opened(*names):
    """Bước đầu của script: agent mở các nguồn (tool của nguồn chỉ có sau khi mở)."""
    return AIMessage("", tool_calls=[{"name": OPEN, "args": {"name": n}, "id": f"open_{n}"} for n in names])


def _add_kho(c, w, name="kho"):
    d = c.post("/mcp/connectors/discover", json={"url": w.resource}).json()
    assert d["auth_type"] == "oauth" and d["issuer"] == w.issuer and d["has_prm"], d
    r = c.post("/mcp/connectors", json={"name": name, "url": w.resource, "auth_type": "oauth", "user_id": "alice"})
    assert r.status_code == 201, r.text
    return r.json()["connector_id"]


def _browser_connect(c, cid, user, a_user=None):
    """Giả lập trình duyệt: popup authorize_url (đã đăng nhập A) → A redirect về callback của MEP."""
    url = c.post(f"/mcp/connectors/{cid}/connect", params={"user_id": user}).json()["authorize_url"]
    resp = httpx.get(url, cookies={"fake_a_user": a_user or user}, follow_redirects=False)
    assert resp.status_code in (302, 307), resp.text
    loc = httpx.URL(resp.headers["location"])
    assert str(loc).startswith("http://localhost:5173/mcp/oauth/callback")
    page = c.get("/mcp/oauth/callback", params=dict(loc.params))
    return json.loads(re.search(r"const msg = (\{.*?\});", page.text).group(1).replace("\\u003c", "<"))


def _tool_results(model):
    """ToolMessage của lượt gần nhất (lời gọi LLM cuối thấy đủ các kết quả tool theo thứ tự), bỏ bước mở nguồn."""
    return [m for m in model.calls[-1] if isinstance(m, ToolMessage) and m.name != OPEN]


def _status(hub, user, name):
    return next(e["status"] for e in hub.catalog(user) if e["name"] == name)


def _expire(pool, user, cid):
    with pool.connection() as conn:
        conn.execute("UPDATE mcp_user_tokens SET expires_at = now() WHERE user_id = %s AND connector_id = %s",
                     (user, cid))


def _token_row(pool, user, cid):
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM mcp_user_tokens WHERE user_id = %s AND connector_id = %s",
                            (user, cid)).fetchone()


# ------------------------------------------------------------------ 1. chỉ bằng URL → agent gọi tool đọc

def test_1_connect_by_url_and_agent_reads(env):
    c, model = env.client([_opened("kho"), tool_call("kho_list_items"), "Kho có Ống thép DN50."])
    with c:
        regs = env.w.a.register_calls
        cid = _add_kho(c, env.w)
        assert env.w.a.register_calls == regs + 1
        msg = _browser_connect(c, cid, "alice")
        assert msg["status"] == "connected", msg
        tools = c.get(f"/mcp/connectors/{cid}/tools", params={"user_id": "alice"}).json()
        assert {t["name"]: t["access"] for t in tools} == {"list_items": "read", "whoami": "read",
                                                           "create_item": "write", "purge": "write"}
        ev = _chat(c, "alice")
        end = _first(ev, "tool_end")
        assert end["tool"] == "kho_list_items" and end["ok"]
        assert "Ống thép DN50" in str(_tool_results(model)[0].content)
        assert _first(ev, "done")["status"] == "OK"
        row = _token_row(env.pool, "alice", cid)
        assert row["status"] == "connected" and not row["access_token_enc"].startswith("ey")


# ------------------------------------------------------------------ 2. tool ghi → confirm_required → đồng ý / từ chối

def test_2_write_tool_confirm_approve_and_reject(env):
    c, model = env.client([_opened("kho"), tool_call("kho_create_item", ten="Van DN50"), "Xong."])
    with c:
        cid = _add_kho(c, env.w)
        _browser_connect(c, cid, "alice")
        for approve in (True, False):
            model.calls.clear()
            before = len(env.w.created)
            ev = _chat(c, "alice", "đẩy vật tư sang kho")
            conf = _first(ev, "confirm_required")
            done = _first(ev, "done")
            assert conf["tool"] == "kho_create_item" and done["status"] == "WAITING_CONFIRM"
            r = c.post("/chat/confirm", json={"conversation_id": done["conversation_id"], "user_id": "alice",
                                              "confirm_id": conf["confirm_id"], "approve": approve})
            ev2 = _sse(r.text)
            assert _first(ev2, "done")["status"] == "OK"
            if approve:
                assert env.w.created[before:] == [("alice@hawee.com.vn", "Van DN50")]
            else:
                assert len(env.w.created) == before


# ------------------------------------------------------------------ 3. access token hết hạn → tự refresh

def test_3_expired_token_auto_refresh(env):
    c, model = env.client([_opened("kho"), tool_call("kho_whoami"), "ok"])
    a = env.w.a
    with c:
        cid = _add_kho(c, env.w)
        _browser_connect(c, cid, "carol")
        old_rt = _token_row(env.pool, "carol", cid)["refresh_token_enc"]
        n = len(a.refresh_grants())
        _expire(env.pool, "carol", cid)                                  # dưới refresh_skew_s → refresh trước khi gọi
        ev = _chat(c, "carol")
        assert _first(ev, "tool_end")["ok"] and len(a.refresh_grants()) == n + 1
        row = _token_row(env.pool, "carol", cid)
        assert row["refresh_token_enc"] != old_rt and row["status"] == "connected"     # refresh token mới đã lưu
        # server trả 401 dù MEP tưởng còn hạn (JWT hết hạn sớm) → refresh 1 lần rồi gọi lại
        a.access_ttl_s, a.advertised_expires_in = 2, 3600
        try:
            _browser_connect(c, cid, "carol")
            time.sleep(3)
            a.access_ttl_s = 3600
            n = len(a.refresh_grants())
            model.calls.clear()
            ev = _chat(c, "carol")
            assert _first(ev, "tool_end")["ok"] and len(a.refresh_grants()) == n + 1
        finally:
            a.access_ttl_s, a.advertised_expires_in = 3600, None


# ------------------------------------------------------------------ 4. A thu hồi refresh token → needs_reauth

def test_4_revoked_refresh_needs_reauth(env):
    c, model = env.client([_opened("kho"), tool_call("kho_list_items"), "Kết nối hết hạn."])
    with c:
        cid = _add_kho(c, env.w)
        _browser_connect(c, cid, "dave")
        env.w.a.revoke("dave")
        _expire(env.pool, "dave", cid)
        ev = _chat(c, "dave")
        end = _first(ev, "tool_end")
        assert not end["ok"] and "Kết nối lại" in end["summary"]
        assert _first(ev, "done")["status"] == "OK"                       # vòng lặp không sập
        st = next(x for x in c.get("/mcp/connectors", params={"user_id": "dave"}).json() if x["connector_id"] == cid)
        assert st["status"] == "needs_reauth"
        assert _status(env.hub, "dave", "kho") == "needs_reauth"
        with pytest.raises(SourceUnavailable):
            env.hub.source_tools("dave", "kho")
        assert _browser_connect(c, cid, "dave")["status"] == "connected"   # bấm Kết nối lại


# ------------------------------------------------------------------ 5. user không có quyền nào → forbidden

def test_5_no_permission_forbidden(env):
    c, _ = env.client(["ok"])
    with c:
        cid = _add_kho(c, env.w)
        msg = _browser_connect(c, cid, "nobody")
        assert msg["status"] == "forbidden" and "chưa được cấp quyền" in msg["message"]
        assert _status(env.hub, "nobody", "kho") == "forbidden"
        with pytest.raises(SourceUnavailable):
            env.hub.source_tools("nobody", "kho")


# ------------------------------------------------------------------ 6. connector none / headers dùng được ngay

def test_6_none_and_headers_connectors(env, monkeypatch):
    monkeypatch.setenv("HDR_API_KEY", API_KEY)
    c, model = env.client([_opened("pub", "hdr"), tool_call("pub_echo", text="xin chào"),
                           tool_call("hdr_echo", text="bí mật"), "ok"])
    with c:
        assert c.post("/mcp/connectors/discover", json={"url": env.w.open_url}).json()["auth_type"] == "none"
        pub = c.post("/mcp/connectors", json={"name": "pub", "url": env.w.open_url, "auth_type": "none"}).json()
        hdr = c.post("/mcp/connectors", json={"name": "hdr", "url": env.w.hdr_url, "auth_type": "headers",
                                              "headers": [{"name": "X-API-Key", "value": "${HDR_API_KEY}"}]}).json()
        assert c.post(f"/mcp/connectors/{pub['connector_id']}/test", params={"user_id": "x"}).json()["ok"]
        assert c.post(f"/mcp/connectors/{hdr['connector_id']}/test", params={"user_id": "x"}).json()["ok"]
        tools = c.get(f"/mcp/connectors/{pub['connector_id']}/tools", params={"user_id": "x"}).json()
        assert {t["name"]: t["access"] for t in tools} == {"echo": "read", "ping": "write"}
        ev = _chat(c, "anyone")
        ends = [d for e, d in ev if e == "tool_end" and d["tool"] != OPEN]
        assert [e["ok"] for e in ends] == [True, True]
        texts = [str(m.content) for m in _tool_results(model)]
        assert "echo: xin chào" in texts[0] and "echo: bí mật" in texts[1]
        c.patch(f"/mcp/connectors/{hdr['connector_id']}", json={"headers": [{"name": "X-API-Key", "value": "sai"}]})
        res = c.post(f"/mcp/connectors/{hdr['connector_id']}/test", params={"user_id": "x"}).json()
        assert not res["ok"] and "401" in res["message"]


# ------------------------------------------------------------------ 7. hai user khác nhau có token riêng

def test_7_two_users_separate_tokens(env):
    c, model = env.client([_opened("kho"), tool_call("kho_whoami"), tool_call("kho_create_item", ten="Bơm"), "ok"])
    with c:
        cid = _add_kho(c, env.w)
        _browser_connect(c, cid, "alice")
        _browser_connect(c, cid, "bob")
        ra, rb = _token_row(env.pool, "alice", cid), _token_row(env.pool, "bob", cid)
        assert BOX.decrypt(ra["access_token_enc"]) != BOX.decrypt(rb["access_token_enc"])
        ev = _chat(c, "bob")
        assert "bob@hawee.com.vn" in str(_tool_results(model)[0].content)
        conf = _first(ev, "confirm_required")
        done = _first(ev, "done")
        ev2 = _sse(c.post("/chat/confirm", json={"conversation_id": done["conversation_id"], "user_id": "bob",
                                                 "confirm_id": conf["confirm_id"], "approve": True}).text)
        end = _first(ev2, "tool_end")
        assert not end["ok"] and "quyền 'write'" in end["summary"]           # ToolError → lỗi có cấu trúc
        assert _token_row(env.pool, "bob", cid)["status"] == "connected"       # kết nối vẫn giữ
        model.calls.clear()
        model.script = [_opened("kho"), tool_call("kho_whoami"), "ok"]
        _chat(c, "alice")
        assert "alice@hawee.com.vn" in str(_tool_results(model)[0].content)


# ------------------------------------------------------------------ refresh đồng thời (Postgres FOR UPDATE)

def test_concurrent_refresh_postgres_single_call(env):
    c, _ = env.client(["ok"])
    with c:
        cid = _add_kho(c, env.w)
        _browser_connect(c, cid, "erin")
        row = env.hub.registry.get(cid)
        _expire(env.pool, "erin", cid)
        n = len(env.w.a.refresh_grants())
        out, errs = [], []

        def worker():
            try:
                out.append(env.hub.tokens.access_token("erin", row))
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errs and len(set(out)) == 1
        assert len(env.w.a.refresh_grants()) == n + 1                          # A chỉ nhận 1 refresh
        live = [r for r in env.w.a.refresh.values() if r["user"] == "erin" and not r["revoked"]]
        assert len(live) == 1                                                  # phiên không bị thu hồi


# ------------------------------------------------------------------ token không lọt log

def test_tokens_not_in_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    c, _ = env.client([tool_call("kho_list_items"), "ok"])
    with c:
        cid = _add_kho(c, env.w)
        _browser_connect(c, cid, "alice")
        _expire(env.pool, "alice", cid)
        _chat(c, "alice")
        row = _token_row(env.pool, "alice", cid)
        secrets = [BOX.decrypt(row["access_token_enc"]), BOX.decrypt(row["refresh_token_enc"])]
        secrets += list(env.w.a.refresh)
    assert caplog.text
    for s in secrets:
        assert s not in caplog.text
