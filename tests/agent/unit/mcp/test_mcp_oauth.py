"""Unit – OAuth phía client: PKCE, khám phá, DCR, state, refresh có khóa, mã hóa (httpx.MockTransport, store in-memory)."""
import datetime as dt
import importlib.util
import re
import threading
from pathlib import Path

import httpx
import pytest

from app.services.agent.mcp.oauth.client_registry import ClientRegistry
from app.services.agent.mcp.oauth.crypto import SecretBox, secret_box
from app.services.agent.mcp.oauth.discovery import check_url, discover
from app.services.agent.mcp.oauth.errors import OAuthError, ReauthRequired, SecretKeyMissing
from app.services.agent.mcp.oauth.flow import TokenSet, consume_state, exchange_code, start
from app.services.agent.mcp.oauth.pkce import challenge_s256, new_verifier
from app.services.agent.mcp.oauth.store import utcnow
from app.services.agent.mcp.oauth.token_store import TokenStore
from tests.agent.unit.mcp.fakes import AS, MCP_URL, REDIRECT, SECRET, FakeAS, MemoryStore, connector_row, mock_http, prm_server

pytestmark = pytest.mark.unit
BOX = SecretBox(SECRET)
A_PKCE = Path(r"D:\ai-planner\app\services\oauth\pkce.py")


# ------------------------------------------------------------------ 1. PKCE

def test_pkce_verifier_and_challenge():
    for _ in range(20):
        v = new_verifier()
        assert 43 <= len(v) <= 128 and re.fullmatch(r"[A-Za-z0-9_\-]+", v)
    # ví dụ trong HUONG_DAN_TICH_HOP_MCP_SERVER.md mục 0.6
    assert challenge_s256("_YFRt0Ys5jlHIF6_yJS5DJU0c_QHJPH2syKgcLmK47I") == "oudi9G1JxaAXL_GedGOUPikK1__vYdo9KRfB5Xf3Pho"
    if A_PKCE.exists():                                   # đối chiếu code verify thật của A
        spec = importlib.util.spec_from_file_location("a_pkce", A_PKCE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        v = new_verifier()
        assert mod.verify(v, challenge_s256(v))


# ------------------------------------------------------------------ 2. Khám phá

def test_discover_401_resource_metadata_then_as():
    fa = FakeAS()
    d = discover(MCP_URL, mock_http(prm_server(), fa.handle))
    assert (d.auth_type, d.resource, d.issuer, d.has_prm, d.registration) == ("oauth", MCP_URL, AS, True, True)
    assert d.as_metadata["token_endpoint"] == f"{AS}/oauth/token"


@pytest.mark.parametrize("suffix,root", [(True, False), (False, True)])
def test_discover_prm_suffix_or_root_without_header(suffix, root):
    d = discover(MCP_URL, mock_http(prm_server(header=False, suffix=suffix, root=root), FakeAS().handle))
    assert d.auth_type == "oauth" and d.issuer == AS and d.has_prm


def test_discover_no_prm_uses_origin_as_issuer():
    origin = "https://mcp.example.com"
    fa = FakeAS(issuer=origin)
    d = discover(MCP_URL, mock_http(prm_server(suffix=False, root=False), fa.handle))
    assert d.auth_type == "oauth" and d.issuer == origin and not d.has_prm and d.resource == MCP_URL


def test_discover_2xx_suggests_none():
    http = mock_http(lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}))
    assert discover(MCP_URL, http).auth_type == "none"


@pytest.mark.parametrize("as_issuer,prm_issuer", [
    ("https://as.example.com/tenant/", "https://as.example.com/tenant"),    # thừa / ở issuer có path
    ("http://as.example.com", "https://as.example.com"),                    # sai scheme
    ("https://as.example.com:441", "https://as.example.com"),               # cổng nội bộ
])
def test_discover_rejects_issuer_mismatch(as_issuer, prm_issuer):
    fa = FakeAS(issuer=as_issuer)

    def as_meta(request):   # AS phục vụ metadata tại URL suy từ issuer trong PRM nhưng khai issuer khác
        if request.url.path.startswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json=fa.metadata())
        return None

    with pytest.raises(OAuthError) as e:
        discover(MCP_URL, mock_http(prm_server(as_list=[prm_issuer]), as_meta))
    assert e.value.code == "issuer_mismatch"


def test_discover_root_issuer_trailing_slash_equivalent():
    """SDK `mcp` ghi PRM gốc dạng 'https://host/' – tương đương issuer 'https://host' (RFC 3986 §6.2.3)."""
    d = discover(MCP_URL, mock_http(prm_server(as_list=[AS + "/"]), FakeAS().handle))
    assert d.issuer == AS


def test_check_url():
    assert check_url(" https://x.vn/mcp ") == "https://x.vn/mcp"
    assert check_url("http://localhost:9000/mcp") and check_url("http://127.0.0.1/mcp")
    for bad in ("http://x.vn/mcp", "ftp://x", "localhost:9000"):
        with pytest.raises(OAuthError):
            check_url(bad)
    with pytest.raises(OAuthError):
        check_url("http://localhost/mcp", allow_http_localhost=False)


# ------------------------------------------------------------------ 3. DCR

def test_dcr_once_per_issuer_redirect():
    fa, store = FakeAS(), MemoryStore()
    reg = ClientRegistry(store, lambda: BOX, lambda: mock_http(fa.handle), "MEP Project Assistant")
    c1 = reg.ensure(AS, fa.metadata(), REDIRECT)
    c2 = reg.ensure(AS, fa.metadata(), REDIRECT)
    assert c1 == c2 and fa.register_calls == 1 and c1.auth_method == "none"
    reg.ensure(AS, fa.metadata(), "https://mep.hawee.com.vn/mcp/oauth/callback")    # đổi redirect_uri → đăng ký lại
    assert fa.register_calls == 2


def test_no_registration_endpoint_requires_manual_client():
    fa, store = FakeAS(registration=False), MemoryStore()
    reg = ClientRegistry(store, lambda: BOX, lambda: mock_http(fa.handle), "MEP")
    with pytest.raises(OAuthError) as e:
        reg.ensure(AS, fa.metadata(), REDIRECT)
    assert e.value.code == "client_id_required"
    meta = {**fa.metadata(), "token_endpoint_auth_methods_supported": ["client_secret_post"]}
    c = reg.save_manual(AS, meta, REDIRECT, "manual-id", "s3cret")
    assert c.auth_method == "client_secret_post" and reg.ensure(AS, meta, REDIRECT) == c
    assert store.clients[(AS, REDIRECT)]["client_secret_enc"] != "s3cret"


# ------------------------------------------------------------------ 4. state

def _start(store, user="u1"):
    from app.services.agent.mcp.oauth.client_registry import ClientInfo

    url = start(store, BOX, user_id=user, connector_id="c1", as_meta=FakeAS().metadata(),
                client=ClientInfo("cid", None, "none"), redirect_uri=REDIRECT, resource=MCP_URL, scope=None, ttl_s=600)
    return httpx.URL(url).params


def test_authorize_url_params():
    p = _start(MemoryStore())
    assert p["response_type"] == "code" and p["code_challenge_method"] == "S256" and p["resource"] == MCP_URL
    assert p["redirect_uri"] == REDIRECT and p["client_id"] == "cid" and "scope" not in p


def test_state_single_use_expiry_and_user():
    store = MemoryStore()
    p = _start(store)
    pend = consume_state(store, BOX, p["state"], user_id="u1")
    assert pend.connector_id == "c1" and challenge_s256(pend.code_verifier) == p["code_challenge"]
    with pytest.raises(OAuthError):
        consume_state(store, BOX, p["state"])                          # dùng lần 2
    p2 = _start(store)
    store.pending[p2["state"]]["expires_at"] = utcnow() - dt.timedelta(seconds=1)
    with pytest.raises(OAuthError):
        consume_state(store, BOX, p2["state"])                         # hết hạn
    p3 = _start(store)
    with pytest.raises(OAuthError):
        consume_state(store, BOX, p3["state"], user_id="u2")           # sai user
    assert p3["state"] not in store.pending                            # đã xóa ngay khi đọc


# ------------------------------------------------------------------ 5–6. token + refresh có khóa

def _token_store(fa, store, **kw):
    http = mock_http(fa.handle)
    reg = ClientRegistry(store, lambda: BOX, lambda: http, "MEP")
    reg.ensure(AS, fa.metadata(), REDIRECT)
    return TokenStore(store, lambda: BOX, lambda: http, reg, REDIRECT, skew_s=60, **kw), reg, http


def _connect(fa, ts, reg, http, user="u1"):
    client = reg.get(AS, REDIRECT)
    v = new_verifier()
    code = fa.issue_code(user, client.client_id, REDIRECT, challenge_s256(v))
    tok = exchange_code(http, fa.metadata(), client, code=code, redirect_uri=REDIRECT, code_verifier=v, resource=MCP_URL)
    ts.save(user, "c1", tok)
    return tok


def test_exchange_code_and_saved_encrypted():
    fa, store = FakeAS(), MemoryStore()
    ts, reg, http = _token_store(fa, store)
    tok = _connect(fa, ts, reg, http)
    row = store.tokens[("u1", "c1")]
    assert row["access_token_enc"] != tok.access_token and BOX.decrypt(row["access_token_enc"]) == tok.access_token
    assert BOX.decrypt(row["refresh_token_enc"]) == tok.refresh_token and row["status"] == "connected"
    assert fa.token_calls[-1]["resource"] == MCP_URL and fa.token_calls[-1]["code_verifier"]
    assert ts.access_token("u1", connector_row()) == tok.access_token          # còn hạn → không refresh
    assert len(fa.token_calls) == 1
    assert "at-" not in repr(tok)


def test_concurrent_refresh_calls_token_endpoint_once():
    fa, store = FakeAS(token_delay_s=0.2), MemoryStore()
    ts, reg, http = _token_store(fa, store)
    old = _connect(fa, ts, reg, http)
    store.tokens[("u1", "c1")]["expires_at"] = utcnow() + dt.timedelta(seconds=5)     # dưới refresh_skew_s
    calls_before = len(fa.token_calls)
    out, errs = [], []

    def worker():
        try:
            out.append(ts.access_token("u1", connector_row()))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs and len(set(out)) == 1 and out[0] != old.access_token
    assert len(fa.token_calls) - calls_before == 1                                     # chỉ 1 lần refresh
    row = store.tokens[("u1", "c1")]
    new_rt = BOX.decrypt(row["refresh_token_enc"])
    assert new_rt != old.refresh_token and not fa.refresh[new_rt]["revoked"]           # refresh token mới đã lưu
    assert not any(fa.refresh[t]["revoked"] for t in fa.refresh if t != old.refresh_token)


def test_refresh_after_401_once_and_reuse_by_other_thread():
    fa, store = FakeAS(), MemoryStore()
    ts, reg, http = _token_store(fa, store)
    old = _connect(fa, ts, reg, http)
    new = ts.refresh("u1", connector_row(), rejected=old.access_token)
    assert new != old.access_token
    n = len(fa.token_calls)
    assert ts.refresh("u1", connector_row(), rejected=old.access_token) == new         # luồng khác đã thay → dùng luôn
    assert len(fa.token_calls) == n


def test_invalid_grant_marks_needs_reauth():
    fa, store = FakeAS(), MemoryStore()
    ts, reg, http = _token_store(fa, store)
    old = _connect(fa, ts, reg, http)
    fa.revoke("u1")                                                                     # A thu hồi phiên
    with pytest.raises(ReauthRequired):
        ts.refresh("u1", connector_row(), rejected=old.access_token)
    assert store.tokens[("u1", "c1")]["status"] == "needs_reauth"
    with pytest.raises(ReauthRequired):
        ts.access_token("u1", connector_row())
    with pytest.raises(ReauthRequired):
        ts.access_token("u2", connector_row())                                          # chưa kết nối


def test_tokenset_expires_at():
    now = utcnow()
    assert TokenSet("a", None, 60, None).expires_at(now) == now + dt.timedelta(seconds=60)
    assert TokenSet("a", None, None, None).expires_at(now) is None


# ------------------------------------------------------------------ 10. Mã hóa

def test_encryption_roundtrip_and_missing_key(monkeypatch):
    enc = BOX.encrypt("bí mật")
    assert enc != "bí mật" and "bí mật" not in enc and BOX.decrypt(enc) == "bí mật"
    assert SecretBox("khóa khác").encrypt("x") != SecretBox("khóa khác").encrypt("x")   # Fernet có IV
    with pytest.raises(OAuthError):
        SecretBox("khóa khác").decrypt(enc)
    monkeypatch.setenv("MEP_SECRET_KEY", "")
    with pytest.raises(SecretKeyMissing):
        secret_box()
    monkeypatch.setenv("MEP_SECRET_KEY", SECRET)
    assert secret_box().decrypt(enc) == "bí mật"
