"""Unit – API agent: SSE mới, upload/tải file, phân quyền, /chat/confirm (mock LLM, MockSandbox, InMemorySaver)."""
import json

import pytest
from fastapi.testclient import TestClient
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from app.main import create_app
from app.providers.llm import MockChatModel
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import ExecResult, SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from tests.agent.fakes import ScriptedModel, tool_call
from tests.query.fakes import make_deps

pytestmark = pytest.mark.unit
U = "test_engineer"
PUSHED = []


@tool("push_data")
def push_data(payload: str) -> str:
    """Đẩy dữ liệu sang hệ thống ngoài (giả lập)."""
    PUSHED.append(payload)
    return json.dumps({"ok": True})


push_data.metadata = {"access": "write"}


def _sse(text):
    out, ev, data = [], None, []
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line.strip() and ev:
            out.append((ev, "\n".join(data)))
            ev, data = None, []
    return out


def _writes_file(sbx, cmd):
    sbx.put_file("output/kết quả.xlsx", b"PK-xlsx")
    return ExecResult(0, "", "")


@pytest.fixture
def client_factory(mock_settings):
    def make(script):
        deps = make_deps(mock_settings, llm=MockChatModel(responses=['{"route": "agent", "reason": "t"}'], calls=[]))
        rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(_writes_file), 1800),
                           model=ScriptedModel(script=script, calls=[]), mcp_tools=[push_data])
        return TestClient(create_app(deps, InMemorySaver(), rt))
    return make


def _chat(client, **body):
    r = client.post("/chat", json={"user_id": U, "question": "x", **body})
    assert r.status_code == 200, r.text
    return _sse(r.text)


def test_chat_agent_sse_and_download(client_factory):
    with client_factory([tool_call("run_code", code="print(1)"), "Đã tạo file."]) as c:
        ev = _chat(c)
        kinds = [e for e, _ in ev]
        assert kinds[0:2] == ["status", "route"] and json.loads(ev[1][1]) == {"route": "agent"}
        assert {"tool_start", "tool_end", "token", "citations", "file", "done"} <= set(kinds)
        f = json.loads(next(d for e, d in ev if e == "file"))
        assert f["name"] == "kết quả.xlsx" and f["size"] == 7
        r = c.get(f["url"])
        assert r.status_code == 200 and r.content == b"PK-xlsx"
        assert "filename*=UTF-8''" in r.headers["content-disposition"]
        cid = json.loads(ev[-1][1])["conversation_id"]
        assert c.get(f"/conversations/{cid}/files/x.xlsx", params={"user_id": "other"}).status_code == 403
        assert c.get(f"/conversations/{cid}/files/khong-co.xlsx", params={"user_id": U}).status_code == 404
        assert c.get(f"/conversations/{cid}/files/..%2Finput%2Fa", params={"user_id": U}).status_code == 400
        conv = c.get(f"/conversations/{cid}").json()
        assert conv["messages"][1]["route"] == "agent" and conv["messages"][1]["files"][0]["url"] == f["url"]


def test_upload_attachment_flow_and_permissions(client_factory):
    with client_factory(["Đã đọc file."]) as c:
        cid = "conv-upload"
        files = {"file": ("../../mẫu.xlsx", b"TEMPLATE")}
        r = c.post(f"/conversations/{cid}/files", params={"user_id": U}, files=files)
        assert r.status_code == 200 and r.json() == {"name": "mẫu.xlsx", "size": 8}       # bỏ phần đường dẫn
        assert c.post(f"/conversations/{cid}/files", params={"user_id": "other"}, files=files).status_code == 403
        ev = _chat(c, conversation_id=cid, attachments=["mẫu.xlsx"])
        assert json.loads(ev[1][1]) == {"route": "agent"}
        bad = c.post("/chat", json={"user_id": U, "question": "x", "conversation_id": cid, "attachments": ["zz.xlsx"]})
        assert bad.status_code == 400
        assert c.post("/chat", json={"user_id": U, "question": "x", "attachments": ["a.xlsx"]}).status_code == 400


def test_upload_size_limit(client_factory):
    with client_factory(["x"]) as c:
        rt = c.app.state.runtime
        rt.cfg = {**rt.cfg, "sandbox": {**rt.cfg["sandbox"], "max_output_file_mb": 0}}
        r = c.post("/conversations/c1/files", params={"user_id": U}, files={"file": ("a.bin", b"xx")})
        assert r.status_code == 413


@pytest.mark.parametrize("approve", [False, True])
def test_confirm_endpoint(client_factory, approve):
    PUSHED.clear()
    with client_factory([tool_call("push_data", payload="RFI"), "Xong."]) as c:
        ev = _chat(c)
        confirm = json.loads(next(d for e, d in ev if e == "confirm_required"))
        done = json.loads(ev[-1][1])
        assert done["status"] == "WAITING_CONFIRM" and confirm["args"] == {"payload": "RFI"} and PUSHED == []
        cid = done["conversation_id"]
        body = {"conversation_id": cid, "user_id": U, "confirm_id": confirm["confirm_id"], "approve": approve}
        assert c.post("/chat/confirm", json={**body, "confirm_id": "sai"}).status_code == 409
        assert c.post("/chat/confirm", json={**body, "user_id": "other"}).status_code == 403
        ev2 = _sse(c.post("/chat/confirm", json=body).text)
        assert json.loads(ev2[-1][1])["status"] == "OK" and PUSHED == (["RFI"] if approve else [])
        assert c.post("/chat/confirm", json=body).status_code == 409                       # không xác nhận 2 lần
