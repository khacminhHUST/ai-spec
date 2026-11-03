"""Unit – connector: registry, httpx.Auth (401 → refresh 1 lần), phân loại đọc/ghi, danh mục nguồn + tool theo
nguồn/user, mô tả nguồn, mở nguồn trong agent, confirm_required và resume khi tool không còn, token không lọt vào log
(MockTransport, store in-memory)."""
import json
import logging

import httpx
import pytest
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from mcp.types import Tool, ToolAnnotations

from app.providers.llm import MockChatModel
from app.services.agent.graph import (
    WAITING_CONFIRM, build_assistant_graph, pending_confirm_ids, stream_assistant_turn, stream_confirm,
)
from app.services.agent.mcp.auth import ConnectorAuth
from app.services.agent.mcp.catalog import SourceUnavailable, source_entry
from app.services.agent.mcp.describe import suggest_description
from app.services.agent.mcp.hub import McpHub
from app.services.agent.mcp.oauth.crypto import SecretBox
from app.services.agent.mcp.oauth.errors import SecretKeyMissing
from app.services.agent.mcp.oauth.pkce import challenge_s256
from app.services.agent.mcp.registry import ConnectorError
from app.services.agent.mcp.session import ServerListing
from app.services.agent.mcp.tools import annotation_access, effective_access
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from tests.agent.fakes import ScriptedModel, tool_call
from tests.agent.unit.mcp.fakes import AS, MCP_URL, SECRET, FakeAS, MemoryStore, mock_http, prm_server
from tests.query.fakes import make_deps

pytestmark = pytest.mark.unit
BOX = SecretBox(SECRET)
CFG = {"public_base_url": "http://localhost:5173", "client_name": "MEP Project Assistant", "tool_cache_ttl_s": 600,
       "refresh_skew_s": 60, "http_timeout_s": 5, "allow_http_localhost": True, "state_ttl_s": 600,
       "servers": {"echo": {"transport": "stdio", "command": "python", "args": ["x.py"]}}}


def _hub(fa=None, *extra_handlers, box=lambda: BOX):
    fa = fa or FakeAS()
    store = MemoryStore()
    hub = McpHub(CFG, store, store, box=box, http=mock_http(prm_server(), fa.handle, *extra_handlers))
    return hub, store, fa


def _oauth_connector(hub):
    return hub.registry.create({"name": "kho", "url": MCP_URL, "auth_type": "oauth"}, "u1")


def _login(hub, fa, row, user):
    """Giả lập trình duyệt: authorize_url → A tự đồng ý → callback."""
    params = httpx.URL(hub.oauth.connect(row["connector_id"], user)).params
    code = fa.issue_code(user, params["client_id"], params["redirect_uri"], params["code_challenge"])
    return hub.oauth.callback(code=code, state=params["state"], error=None)


def _tools(*specs):
    return [Tool(name=n, description=f"tool {n}", inputSchema={"type": "object", "properties": {}}, annotations=a)
            for n, a in specs]


# ------------------------------------------------------------------ registry

def test_create_oauth_connector_discovers_and_registers_once():
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    assert (row["resource"], row["issuer"]) == (MCP_URL, AS) and row["as_metadata"]["token_endpoint"]
    hub.registry.create({"name": "kho2", "url": MCP_URL, "auth_type": "oauth"}, "u1")
    assert fa.register_calls == 1                                   # cùng issuer + redirect_uri → DCR 1 lần
    with pytest.raises(ConnectorError):
        hub.registry.create({"name": "kho", "url": MCP_URL, "auth_type": "none"}, "u1")      # trùng tên
    with pytest.raises(ConnectorError):
        hub.registry.create({"name": "echo", "url": MCP_URL, "auth_type": "none"}, "u1")     # trùng yaml
    with pytest.raises(ConnectorError):
        hub.registry.create({"name": "Kho X", "url": MCP_URL, "auth_type": "none"}, "u1")    # sai slug


def test_headers_encrypted_env_substituted_and_hidden(monkeypatch):
    monkeypatch.setenv("KHO_API_KEY", "k-123")
    hub, store, _ = _hub()
    row = hub.registry.create({"name": "h1", "url": "https://h.example.com/mcp", "auth_type": "headers",
                               "headers": [{"name": "X-API-Key", "value": "${KHO_API_KEY}"}]}, "u1")
    assert "KHO_API_KEY" not in row["headers_enc"] and "X-API-Key" not in row["headers_enc"]
    assert hub.registry.headers_for(row) == {"X-API-Key": "k-123"}
    views = hub.list_connectors("u1")
    v = next(c for c in views if c["name"] == "h1")
    assert v["header_names"] == ["X-API-Key"] and v["status"] == "ready" and "headers_enc" not in v
    y = next(c for c in views if c["name"] == "echo")
    assert y["readonly"] and y["source"] == "yaml" and y["connector_id"] == "yaml:echo"


def test_missing_secret_key_blocks_oauth_and_headers_but_not_none():
    def no_key():
        raise SecretKeyMissing()

    hub, _, _ = _hub(box=no_key)
    with pytest.raises(SecretKeyMissing):
        _oauth_connector(hub)
    with pytest.raises(SecretKeyMissing):
        hub.registry.create({"name": "h1", "url": MCP_URL, "auth_type": "headers", "headers": {"A": "b"}}, "u1")
    assert hub.registry.create({"name": "n1", "url": MCP_URL, "auth_type": "none"}, "u1")["auth_type"] == "none"


def test_update_url_resets_tokens_and_delete_cascades(monkeypatch):
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 0})
    assert _login(hub, fa, row, "u1")["status"] == "connected"
    _, reset = hub.registry.update(row["connector_id"], {"display_name": "Kho vật tư"})
    assert not reset and ("u1", row["connector_id"]) in store.tokens
    hub.registry.update(row["connector_id"], {"url": "https://mcp.example.com/mcp?v=2"})   # đổi URL → khám phá lại
    assert ("u1", row["connector_id"]) not in store.tokens
    hub.registry.delete(row["connector_id"])
    assert store.get_connector(row["connector_id"]) is None


# ------------------------------------------------------------------ OAuth kết nối + callback

def test_connect_callback_and_errors(monkeypatch):
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 2})
    res = _login(hub, fa, row, "u1")
    assert res == {"connector_id": row["connector_id"], "status": "connected", "message": "Đã kết nối"}
    assert store.token_statuses("u1") == {row["connector_id"]: "connected"} and store.token_statuses("u2") == {}
    # state đã dùng / A trả lỗi invalid_target
    params = httpx.URL(hub.oauth.connect(row["connector_id"], "u2")).params
    bad = hub.oauth.callback(code=None, state=params["state"], error="invalid_target")
    assert bad["status"] == "error" and "chưa được quản trị" in bad["message"]
    again = hub.oauth.callback(code="x", state=params["state"], error=None)
    assert again["status"] == "error" and again["connector_id"] is None
    # cookie người khởi tạo khác user của state → từ chối
    params = httpx.URL(hub.oauth.connect(row["connector_id"], "u2")).params
    assert hub.oauth.callback(code="x", state=params["state"], error=None, user_hint="u1")["status"] == "error"
    assert "Đã xóa token" in hub.oauth.disconnect(row["connector_id"], "u1")
    assert store.token_statuses("u1") == {}


# ------------------------------------------------------------------ 6. httpx.Auth: 401 → refresh 1 lần

def _mcp_endpoint(accept: set[str], seen: list[str], status_other=401, www=""):
    def handle(request):
        if request.url.path != "/mcp" or request.method != "POST":
            return None
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        seen.append(token)
        if token in accept:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(status_other, headers={"WWW-Authenticate": www} if www else {})
    return handle


def test_auth_401_refresh_once_then_retry(monkeypatch):
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 0})
    _login(hub, fa, row, "u1")
    old = BOX.decrypt(store.tokens[("u1", row["connector_id"])]["access_token_enc"])
    accept, seen = set(), []
    client = httpx.Client(transport=httpx.MockTransport(_mcp_endpoint(accept, seen)),
                          auth=ConnectorAuth(hub.tokens, "u1", row))
    fa_token = fa.token

    def token_and_accept(form):                    # token mới phát ra được server MCP chấp nhận
        resp = fa_token(form)
        if resp.status_code == 200:
            accept.add(resp.json()["access_token"])
        return resp

    fa.token = token_and_accept
    n = len(fa.token_calls)
    assert client.post(f"{MCP_URL}").status_code == 200
    assert len(seen) == 2 and seen[0] == old and seen[1] != old and len(fa.token_calls) == n + 1
    assert store.tokens[("u1", row["connector_id"])]["status"] == "connected"


def test_auth_still_401_or_invalid_grant_needs_reauth(monkeypatch):
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 0})
    _login(hub, fa, row, "u1")
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(_mcp_endpoint(set(), seen)), auth=ConnectorAuth(hub.tokens, "u1", row))
    assert client.post(MCP_URL).status_code == 401 and len(seen) == 2          # chỉ gửi lại 1 lần
    assert store.tokens[("u1", row["connector_id"])]["status"] == "needs_reauth"
    # A thu hồi refresh token → invalid_grant → needs_reauth
    _login(hub, fa, row, "u1")
    fa.revoke("u1")
    from app.services.agent.mcp.oauth.errors import ReauthRequired
    with pytest.raises(ReauthRequired):
        client.post(MCP_URL)
    assert store.tokens[("u1", row["connector_id"])]["status"] == "needs_reauth"
    _, msg = hub.tools.explain(row, ReauthRequired())
    assert "Kết nối lại" in msg


def test_auth_403_insufficient_scope_marks_forbidden(monkeypatch):
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 0})
    _login(hub, fa, row, "u1")
    client = httpx.Client(transport=httpx.MockTransport(_mcp_endpoint(set(), [], 403, 'Bearer error="insufficient_scope"')),
                          auth=ConnectorAuth(hub.tokens, "u1", row))
    assert client.post(MCP_URL).status_code == 403
    assert store.tokens[("u1", row["connector_id"])]["status"] == "forbidden"


# ------------------------------------------------------------------ 7. phân loại đọc/ghi

def test_access_classification_precedence():
    ro, destr, plain = ToolAnnotations(readOnlyHint=True), ToolAnnotations(destructiveHint=True), ToolAnnotations()
    t_ro, t_destr, t_plain, t_none = _tools(("a", ro), ("b", destr), ("c", plain), ("d", None))
    assert [annotation_access(t) for t in (t_ro, t_destr, t_plain, t_none)] == ["read", "write", "write", "write"]
    assert effective_access(t_ro, "write") == "write" and effective_access(t_destr, "read") == "read"
    assert effective_access(t_ro, "disabled") == "disabled" and effective_access(t_ro, "lạ") == "read"


# ------------------------------------------------------------------ 8. danh mục nguồn + tool theo nguồn / user

def test_catalog_and_source_tools_by_user_status_enabled_and_override(monkeypatch):
    hub, store, fa = _hub()
    kho = _oauth_connector(hub)
    open_ = hub.registry.create({"name": "pub", "url": "https://pub.example.com/mcp", "auth_type": "none",
                                 "description": "  Kho  công khai "}, "u1")
    off = hub.registry.create({"name": "off", "url": "https://off.example.com/mcp", "auth_type": "none",
                               "enabled": False}, "u1")
    listed = {kho["connector_id"]: _tools(("list_items", ToolAnnotations(readOnlyHint=True)), ("create_item", None)),
              open_["connector_id"]: _tools(("echo", ToolAnnotations(readOnlyHint=True))),
              off["connector_id"]: _tools(("x", None))}
    monkeypatch.setattr(hub.tools, "mcp_tools", lambda row, user, fresh=False: listed[row["connector_id"]])
    cat = lambda user: {e["name"]: e["status"] for e in hub.catalog(user)}             # noqa: E731
    names = lambda user, src: {t.name: t.metadata["access"] for t in hub.source_tools(user, src)}   # noqa: E731
    assert cat("u1") == {"kho": "not_connected", "pub": "ready"}                       # connector tắt → không có
    assert next(e for e in hub.catalog("u1") if e["name"] == "pub")["description"] == "Kho công khai"
    assert names("u1", "pub") == {"pub_echo": "read"}
    for src in ("kho", "off", "khong_co"):
        with pytest.raises(SourceUnavailable):
            hub.source_tools("u1", src)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 2})
    _login(hub, fa, kho, "u1")
    assert cat("u1")["kho"] == "connected" and cat("u2")["kho"] == "not_connected"     # u2 chưa kết nối
    assert names("u1", "kho") == {"kho_list_items": "read", "kho_create_item": "write"}
    store.set_override(kho["connector_id"], "create_item", "disabled")
    store.set_override(kho["connector_id"], "list_items", "write")
    assert names("u1", "kho") == {"kho_list_items": "write"}
    store.tokens[("u1", kho["connector_id"])]["status"] = "needs_reauth"
    assert cat("u1")["kho"] == "needs_reauth"
    with pytest.raises(SourceUnavailable, match="Kết nối lại"):
        hub.source_tools("u1", "kho")


def test_auth_is_bound_to_chatting_user(monkeypatch):
    """User A gọi tool → header mang token của A, không bao giờ của B."""
    hub, store, fa = _hub()
    kho = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 0})
    _login(hub, fa, kho, "alice")
    _login(hub, fa, kho, "bob")
    tok = {u: BOX.decrypt(store.tokens[(u, kho["connector_id"])]["access_token_enc"]) for u in ("alice", "bob")}
    assert tok["alice"] != tok["bob"] and tok["alice"].startswith("at-alice")
    for user in ("alice", "bob"):
        seen = []
        conn = hub.tools.connection_for(kho, user)
        httpx.Client(transport=httpx.MockTransport(_mcp_endpoint(set(tok.values()), seen)), auth=conn["auth"]).post(MCP_URL)
        assert seen == [tok[user]]


def test_connector_load_error_is_reported(monkeypatch, caplog):
    hub, _, _ = _hub()
    hub.registry.create({"name": "pub", "url": "https://pub.example.com/mcp", "auth_type": "none"}, "u1")

    def boom(row, user, fresh=False):
        raise ConnectionError("mất mạng")

    monkeypatch.setattr(hub.tools, "mcp_tools", boom)
    with caplog.at_level(logging.WARNING), pytest.raises(SourceUnavailable, match="mất mạng"):
        hub.source_tools("u1", "pub")
    assert "pub" in caplog.text
    assert [e["name"] for e in hub.catalog("u1")] == ["pub"]                           # danh mục không gọi server


# ------------------------------------------------------------------ mô tả nguồn

def test_probe_fills_empty_description_only(monkeypatch):
    hub, store, _ = _hub()
    row = hub.registry.create({"name": "mail", "url": "https://mail.example.com/mcp", "auth_type": "none"}, "u1")
    listing = ServerListing(tools=_tools(("search", None)), instructions="Tools for reading the  mailbox.")
    monkeypatch.setattr(hub.tools, "listing", lambda r, u: listing)
    assert hub.tools.probe(row, "u1")["ok"]
    assert store.get_connector(row["connector_id"])["description"] == "Tools for reading the mailbox."
    hub.registry.update(row["connector_id"], {"description": "Hộp thư công ty"})
    hub.tools.probe(hub.registry.get(row["connector_id"]), "u1")
    assert store.get_connector(row["connector_id"])["description"] == "Hộp thư công ty"    # không ghi đè mô tả đã có


def test_suggest_description_order():
    row = {"name": "kho", "display_name": "Kho vật tư"}
    tools = _tools(("list_items", None), ("create_item", None))
    assert suggest_description(row, ServerListing(tools=tools, about="Kho – vật tư"), None) == "Kho – vật tư"
    llm = MockChatModel(responses=["Tra cứu và tạo vật tư trong kho."], calls=[])
    assert suggest_description(row, ServerListing(tools=tools), llm) == "Tra cứu và tạo vật tư trong kho."
    assert "Kho vật tư" in str(llm.calls[0][-1].content) and "list_items" in str(llm.calls[0][-1].content)
    assert suggest_description(row, ServerListing(tools=tools), None) == "Công cụ: list_items, create_item"
    broken = MockChatModel(handler=lambda m: (_ for _ in ()).throw(RuntimeError("x")), calls=[])
    assert suggest_description(row, ServerListing(tools=tools), broken) == "Công cụ: list_items, create_item"
    assert suggest_description(row, ServerListing(), None) == ""


# ------------------------------------------------------------------ 7. tool write → confirm_required; resume khi tool mất

CREATED = []


@tool("kho_create_item")
def kho_create_item(ten: str) -> str:
    """Tạo vật tư (giả lập tool connector write)."""
    CREATED.append(ten)
    return json.dumps({"ok": True})


kho_create_item.metadata = {"access": "write", "mcp_server": "kho"}


class FakeHub:
    """Hub giả: 1 nguồn `kho` với 1 tool ghi."""

    def __init__(self):
        self.tools = [kho_create_item]
        self.status = "connected"

    def catalog(self, user_id):
        return [source_entry("kho", "Kho vật tư", "Tồn kho, nhập xuất vật tư", self.status)]

    def source_tools(self, user_id, name):
        if name != "kho" or not self.tools:
            raise SourceUnavailable("Kho vật tư: đã ngắt kết nối")
        return list(self.tools)


def _open(name="kho"):
    return tool_call("open_source_tools", name=name)


def _graph(mock_settings, hub, script):
    deps = make_deps(mock_settings, llm=MockChatModel(responses=["x"], calls=[]))
    model = ScriptedModel(script=script, calls=[])
    rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800), model=model, yaml_tools=[],
                       mcp=hub)
    return build_assistant_graph(deps, rt, InMemorySaver()), model


def test_connector_write_tool_confirm_then_approve(mock_settings):
    CREATED.clear()
    hub = FakeHub()
    graph, _ = _graph(mock_settings, hub, [_open(), tool_call("kho_create_item", ten="ống thép"), "Đã tạo."])
    ev = list(stream_assistant_turn(graph, "u1", "đẩy vật tư sang kho"))
    conf = next(e["data"] for e in ev if e["event"] == "confirm_required")
    done = next(e["data"] for e in ev if e["event"] == "done")
    assert conf["tool"] == "kho_create_item" and done["status"] == WAITING_CONFIRM and CREATED == []
    ev2 = list(stream_confirm(graph, done["conversation_id"], conf["confirm_id"], True))
    assert CREATED == ["ống thép"] and next(e["data"] for e in ev2 if e["event"] == "done")["status"] == "OK"


def test_catalog_in_prompt_tools_only_after_open_and_kept_next_turn(mock_settings):
    """Agent chỉ thấy danh mục; tool của nguồn có từ bước sau khi mở; nguồn đã mở giữ sang lượt sau."""
    hub = FakeHub()
    graph, model = _graph(mock_settings, hub, [_open(), "Đã mở kho."])
    ev = list(stream_assistant_turn(graph, "u1", "tồn kho ống thép?"))
    cid = next(e["data"] for e in ev if e["event"] == "done")["conversation_id"]
    system = str(model.calls[0][0].content)
    assert "- kho (Kho vật tư): Tồn kho, nhập xuất vật tư [đã kết nối]" in system
    assert "kho_create_item" not in model.bound[0] and "open_source_tools" in model.bound[0]
    assert "kho_create_item" in model.bound[-1] and "[đã mở]" in str(model.calls[-1][0].content)
    end = next(e["data"] for e in ev if e["event"] == "tool_end")
    assert end == {"tool": "open_source_tools", "ok": True, "summary": "kho: 1 công cụ"}
    model.calls.clear()
    model.bound.clear()
    model.script = ["Lượt 2."]
    list(stream_assistant_turn(graph, "u1", "còn gì nữa?", cid))
    assert "kho_create_item" in model.bound[0]                                           # không cần mở lại


def test_open_source_not_connected_is_structured_error(mock_settings):
    hub = FakeHub()
    hub.status = "not_connected"
    graph, model = _graph(mock_settings, hub, [_open(), _open("khac"), "Bạn cần kết nối kho."])
    ev = list(stream_assistant_turn(graph, "u1", "tồn kho?"))
    ends = [e["data"] for e in ev if e["event"] == "tool_end"]
    assert [e["ok"] for e in ends] == [False, False] and "bấm Kết nối" in ends[0]["summary"]
    assert "Không có nguồn 'khac'" in ends[1]["summary"]
    assert all("kho_create_item" not in b for b in model.bound)
    msgs = [m for m in model.calls[-1] if isinstance(m, ToolMessage)]
    assert json.loads(msgs[1].content)["available"] == ["kho"]


def test_resume_after_tool_removed_returns_structured_error(mock_settings):
    CREATED.clear()
    hub = FakeHub()
    graph, _ = _graph(mock_settings, hub, [_open(), tool_call("kho_create_item", ten="x"), "Không thực hiện được."])
    ev = list(stream_assistant_turn(graph, "u1", "đẩy vật tư"))
    conf = next(e["data"] for e in ev if e["event"] == "confirm_required")
    cid = next(e["data"] for e in ev if e["event"] == "done")["conversation_id"]
    hub.tools = []                                                     # vừa ngắt kết nối
    ev2 = list(stream_confirm(graph, cid, conf["confirm_id"], True))
    end = next(e["data"] for e in ev2 if e["event"] == "tool_end")
    assert end["tool"] == "kho_create_item" and not end["ok"] and "không có trong bộ tool" in end["summary"]
    assert CREATED == [] and next(e["data"] for e in ev2 if e["event"] == "done")["status"] == "OK"
    assert pending_confirm_ids(graph, cid) == []


# ------------------------------------------------------------------ 9. token không lọt vào log

def test_tokens_never_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    hub, store, fa = _hub()
    row = _oauth_connector(hub)
    monkeypatch.setattr(hub.tools, "probe", lambda r, u: {"ok": True, "status": "connected", "message": "", "tool_count": 0})
    _login(hub, fa, row, "u1")
    client = httpx.Client(transport=httpx.MockTransport(_mcp_endpoint(set(), [])), auth=ConnectorAuth(hub.tokens, "u1", row))
    client.post(MCP_URL)                                               # 401 → refresh → 401 → needs_reauth
    _login(hub, fa, row, "u1")
    fa.revoke("u1")
    hub.tokens.store.tokens[("u1", row["connector_id"])]["expires_at"] = None
    try:
        hub.tokens.refresh("u1", row, rejected="khác")
    except Exception:  # noqa: BLE001
        pass
    secrets = [c.get("refresh_token") or c.get("code") for c in fa.token_calls] + list(fa.refresh)
    secrets += [t for c in fa.token_calls for t in [c.get("code_verifier")] if t]
    for (u, cid), r in store.tokens.items():
        secrets.append(BOX.decrypt(r["access_token_enc"]))
    secrets = [s for s in secrets if s]
    assert secrets and caplog.text
    for s in secrets:
        assert s not in caplog.text
    assert challenge_s256  # dùng import
