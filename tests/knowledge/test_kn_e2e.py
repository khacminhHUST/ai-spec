"""E2E (API thật, namespace tạm – không đụng `ingest`): upload Spec Điện S5 qua API → worker → phát hành →
câu hỏi vàng #1 trả đúng "6 lần đường kính ngoài"."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.routes.knowledge import get_store
from app.main import create_app
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.knowledge import worker
from app.services.knowledge.cache import attach_store_version
from app.services.query.deps import Deps
from app.services.query.graph import build_graph, run_turn
from tests.knowledge.helpers import BASE_CODE, PROJECT
from tests.query.test_q_golden import values_found

pytestmark = pytest.mark.e2e
GOLDEN = Path(__file__).parents[1] / "fixtures" / "s5_golden.yaml"


def test_upload_phat_hanh_spec_dien_hoi_cau_vang_1(test_ns, store, tmp_path, monkeypatch):
    s = test_ns
    folder = s.data_root / s.project_list()[0]["buildings"][0]["source_root"] / "2_Yeu cau ky thuat (Spec)"
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        pytest.skip(f"Không có Spec Điện S5: {folder}")
    monkeypatch.setattr("app.services.knowledge.worker.artifacts_dir", lambda: tmp_path / "artifacts")
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    q1 = next(q for q in golden["questions"] if q["id"] == 1)

    deps = Deps.default(s)
    attach_store_version(deps)
    rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800), mcp_tools=[])
    app = create_app(deps, InMemorySaver(), rt)
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        meta = {"project_id": PROJECT, "building": "S5", "stream": "DESIGN_CONSULTANT", "disciplines": ["ELECTRICAL"],
                "doc_type": "SPEC", "series": {"mode": "new"}}
        with open(pdfs[0], "rb") as f:
            r = client.post("/knowledge/uploads", files={"file": (pdfs[0].name, f, "application/pdf")},
                            data={"metadata": json.dumps(meta), "user_id": "e2e"})
        assert r.status_code == 201, r.text
        job_id = r.json()["job_id"]
        assert worker.run_once(s, store=store) == job_id
        job = client.get(f"/knowledge/jobs/{job_id}").json()
        assert job["status"] == "DRAFT", job["error"]
        client.post(f"/knowledge/jobs/{job_id}/publish", json={"user_id": "e2e"})
        assert worker.run_once(s, store=store) == job_id
        job = client.get(f"/knowledge/jobs/{job_id}").json()
        assert job["status"] == "PUBLISHED", job["error"]

        out = run_turn(build_graph(deps, InMemorySaver()), "e2e", q1["question"])
        assert out["status"] == "OK"
        assert all(values_found(out["answer"], q1["expected_values"])), out["answer"]
        assert any(b["doc_id"] == job["doc_id"] for b in out["context"])
        assert client.get(f"/knowledge/series/{BASE_CODE}").json()["revisions"][0]["doc_id"] == job["doc_id"]
