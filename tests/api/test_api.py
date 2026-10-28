"""Unit – API FastAPI (mock deps, InMemorySaver)."""
import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.main import create_app
from tests.query.fakes import make_deps, router_json, scripted_llm

pytestmark = pytest.mark.unit


def _sse(text: str) -> list[tuple[str, str]]:
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


def test_chat_sse_and_conversations(mock_settings):
    llm = scripted_llm(router_json(q="Thời gian bảo hành hệ thống điện", queries_vi=["thời gian bảo hành hệ thống điện"]),
                       answer="Bảo hành 24 tháng [1].")
    deps = make_deps(mock_settings, llm=llm)
    with TestClient(create_app(deps, InMemorySaver())) as client:
        r = client.post("/chat", json={"user_id": "test_engineer", "question": "Bảo hành bao lâu?"})
        assert r.status_code == 200
        events = _sse(r.text)
        kinds = [e for e, _ in events]
        assert {"status", "token", "citations", "done"} <= set(kinds)
        done = json.loads(events[-1][1])
        cites = json.loads(next(d for e, d in events if e == "citations"))
        assert done["status"] == "OK" and cites[0]["section_key"] == "sec_5_23"
        cid = done["conversation_id"]

        convs = client.get("/conversations", params={"user_id": "test_engineer"}).json()
        assert [c["conversation_id"] for c in convs] == [cid]
        conv = client.get(f"/conversations/{cid}").json()
        assert len(conv["messages"]) == 2 and conv["messages"][1]["content"] == "Bảo hành 24 tháng [1]."
        assert client.get("/conversations/khong-co").status_code == 404
        r2 = client.post("/chat", json={"user_id": "other", "question": "x", "conversation_id": cid})
        assert r2.status_code == 403
