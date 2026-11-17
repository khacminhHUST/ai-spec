"""Unit – middleware MEP_AUTH_MODE=trusted_proxy (app/api/trusted_proxy.py): đọc email từ JWT, ghi đè user_id,
chặn đọc hội thoại người khác, giữ SSE stream, đường miễn kiểm (healthz, callback OAuth)."""
from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Form, Query
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.trusted_proxy import TrustedProxyAuth, identity

pytestmark = pytest.mark.unit


def jwt(payload: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload)}.chuky-khong-kiem"


A = jwt({"sub": "a@hawee.com.vn", "type": "access", "exp": time.time() + 600})
B_EMAIL = jwt({"email": "b@hawee.com.vn", "sub": "42", "exp": time.time() + 600})


def test_identity():
    assert identity(A) == "a@hawee.com.vn"
    assert identity(B_EMAIL) == "b@hawee.com.vn"                       # ưu tiên trường email
    assert identity(B_EMAIL, claim="sub") == "42"
    assert identity(jwt({"sub": "a@x", "type": "refresh"})) is None     # refresh token không dùng được
    assert identity(jwt({"sub": "a@x", "exp": time.time() - 1})) is None
    assert identity("khong-phai-jwt") is None and identity(None) is None and identity(jwt({"exp": 1e12})) is None


class Body(BaseModel):
    user_id: str
    question: str


@pytest.fixture
def client():
    app = FastAPI()
    convs = {"c1": {"user_id": "a@hawee.com.vn"}, "c2": {"user_id": "khac@hawee.com.vn"}}
    app.state.deps = SimpleNamespace(repo=SimpleNamespace(get_conversation=convs.get))

    @app.get("/conversations")
    def ls(user_id: str = Query(...)):
        return {"user_id": user_id}

    @app.get("/conversations/{cid}")
    def get(cid: str):
        return {"cid": cid}

    @app.post("/chat")
    def chat(b: Body):
        def gen():
            yield f"data: {b.user_id}\n\n"
            yield "data: done\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/knowledge/uploads")
    def upload(user_id: str = Form(...)):
        return {"user_id": user_id}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/mcp/oauth/callback")
    def cb(state: str = ""):
        return {"state": state}

    app.add_middleware(TrustedProxyAuth)
    return TestClient(app)


H = {"Authorization": f"Bearer {A}"}


def test_khong_token_thi_401_tru_duong_mien(client):
    assert client.get("/conversations", params={"user_id": "x"}).status_code == 401
    assert client.get("/conversations", params={"user_id": "x"},
                      headers={"Authorization": "Bearer " + jwt({"sub": "a", "type": "refresh"})}).status_code == 401
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/mcp/oauth/callback", params={"state": "s1"}).json() == {"state": "s1"}


def test_ghi_de_user_id_query_va_json(client):
    assert client.get("/conversations", params={"user_id": "gia_mao"}, headers=H).json() == {"user_id": "a@hawee.com.vn"}
    r = client.post("/chat", json={"user_id": "gia_mao", "question": "q"}, headers=H)
    assert r.status_code == 200 and r.text == "data: a@hawee.com.vn\n\ndata: done\n\n"   # SSE vẫn chạy


def test_doc_hoi_thoai_nguoi_khac_bi_chan(client):
    assert client.get("/conversations/c1", headers=H).json() == {"cid": "c1"}
    assert client.get("/conversations/c2", headers=H).status_code == 403
    assert client.get("/conversations/moi", headers=H).status_code == 200   # chưa có → route tự xử lý


def test_multipart_khong_bi_sua(client):
    r = client.post("/knowledge/uploads", data={"user_id": "a@hawee.com.vn"}, headers=H)
    assert r.json() == {"user_id": "a@hawee.com.vn"}


def test_trithuc_main_healthz_va_middleware(monkeypatch):
    """Điểm khởi chạy riêng: /healthz không cần token; bật trusted_proxy thì route khác đòi token (không chạy lifespan)."""
    from app.trithuc_main import create_trithuc_app

    monkeypatch.setenv("MEP_AUTH_MODE", "trusted_proxy")
    c = TestClient(create_trithuc_app())
    assert c.get("/healthz").json() == {"ok": True}
    assert c.get("/projects").status_code == 401
    monkeypatch.delenv("MEP_AUTH_MODE")
    assert not any(m.cls is TrustedProxyAuth for m in create_trithuc_app().user_middleware)
