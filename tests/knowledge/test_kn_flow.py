"""Integration (docs/knowledge-admin.md mục 9): API + worker trên Postgres/Qdrant thật (namespace tạm), LLM/embedding mock.

Upload trích đoạn Spec Điện S5 → nháp (DRAFT, có sec_5_7) → phát hành → tìm thấy trong Query Pipeline không cần
khởi động lại; revision mới của bộ thay bản cũ; đặt lại bản cũ; gỡ; xóa; hủy job; sửa alias dùng ngay.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from qdrant_client import models as qm

from app.api.routes.knowledge import get_store
from app.db.client import pg_connect, pg_pool, qdrant
from app.main import create_app
from app.providers.reranker import MockReranker
from app.repositories.postgres import PgRepo
from app.repositories.qdrant import QdrantRetriever
from app.services.agent.runtime import build_runtime
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.knowledge import worker
from app.services.knowledge.cache import attach_store_version
from app.services.query.deps import Deps
from app.services.query.graph import build_graph, run_turn
from app.services.query.resolver import load_profiles, resolve
from tests.agent.fakes import ScriptedModel
from tests.knowledge.helpers import BASE_CODE, PROJECT, SPEC_META, SPEC_PDF, variant_pdf
from tests.query.fakes import approx_tokens, router_json, scripted_llm

pytestmark = pytest.mark.integration
QUESTION = "Bán kính uốn cong tối thiểu của cáp tiết diện lớn hơn 25mm² là bao nhiêu?"


@pytest.fixture
def env(kn_ns, store, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.knowledge.worker.artifacts_dir", lambda: tmp_path / "artifacts")
    pool = pg_pool(kn_ns, min_size=1, max_size=4)
    llm = scripted_llm(router_json(q=QUESTION, project_action="set", project_mentions=["S5"],
                                   queries_vi=["bán kính uốn cong tối thiểu cáp lớn hơn 25mm2"],
                                   exact_keywords=["25mm2"]), answer="6 lần đường kính ngoài [1].")
    deps = Deps(settings=kn_ns, repo=PgRepo(kn_ns, pool, mode="open"), retriever=QdrantRetriever(kn_ns),
                reranker=MockReranker(), llm_fast=llm, llm_main=llm, count_tokens=approx_tokens)
    attach_store_version(deps)      # như app/main.py: worker (tiến trình riêng) phát hành → làm mới cache
    rt = build_runtime(deps, manager=SandboxManager(lambda cid: MockSandbox(), 1800),
                       model=ScriptedModel(script=[], calls=[]), mcp_tools=[])
    app = create_app(deps, InMemorySaver(), rt)
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield kn_ns, client, deps, store
    pool.close()


def upload(client, path, user="kysu_a", **meta):
    with open(path, "rb") as f:
        return client.post("/knowledge/uploads", files={"file": (path.name, f, "application/pdf")},
                           data={"metadata": json.dumps({**SPEC_META, **meta}), "user_id": user})


def run_worker(s, store):
    assert worker.run_once(s, store=store) is not None


def search_counts(s, doc_id=None) -> dict:
    with pg_connect(s) as conn:
        where, args = ("WHERE doc_id = %s", (doc_id,)) if doc_id else ("", ())
        docs = conn.execute(f"SELECT count(*) AS n FROM documents {where}", args).fetchone()["n"]
    flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]) if doc_id else None
    pts = sum(qdrant(s).count(s.collection(lang), count_filter=flt, exact=True).count for lang in ("vi", "en"))
    return {"documents": docs, "points": pts}


def qdrant_latest(s, doc_id) -> set[bool]:
    flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    out = set()
    for lang in ("vi", "en"):
        pts, _ = qdrant(s).scroll(s.collection(lang), scroll_filter=flt, limit=500, with_payload=["is_latest"])
        out |= {p.payload["is_latest"] for p in pts}
    return out


def pg_latest(s, doc_id) -> bool:
    with pg_connect(s) as conn:
        return conn.execute("SELECT is_latest FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()["is_latest"]


def test_upload_nhap_phat_hanh_revision(env, tmp_path):
    s, client, deps, store = env
    assert {o["value"] for o in client.get("/knowledge/options").json()["doc_types"]} >= {"SPEC", "CONTRACT"}
    assert load_profiles(deps, [PROJECT])[PROJECT]["docs"] == {}      # cache hồ sơ: chưa có tài liệu

    # ---- upload → QUEUED; trùng / không phải PDF bị từ chối
    r = upload(client, SPEC_PDF)
    assert r.status_code == 201, r.text
    job = r.json()
    assert (job["status"], job["series_doc_code"], job["uploaded_by"]) == ("QUEUED", BASE_CODE, "kysu_a")
    assert store.exists(job["storage_key"])
    assert upload(client, SPEC_PDF).status_code == 409
    bad = client.post("/knowledge/uploads", files={"file": ("x.docx", b"PK..", "application/octet-stream")},
                      data={"metadata": json.dumps(SPEC_META), "user_id": "u"})
    assert bad.status_code == 415
    assert client.post(f"/knowledge/jobs/{job['job_id']}/publish", json={}).status_code == 409   # chưa DRAFT

    # ---- worker → DRAFT, có report + cây mục; nháp không vào DB tìm kiếm
    run_worker(s, store)
    j = client.get(f"/knowledge/jobs/{job['job_id']}").json()
    assert j["status"] == "DRAFT", j["error"]
    assert j["report"]["langs"]["vi"]["n_parents"] > 0 and j["replaces"] is None
    secs = client.get(f"/knowledge/jobs/{job['job_id']}/sections").json()
    assert {("vi", "sec_5_7"), ("en", "sec_5_7")} <= {(x["lang"], x["section_key"]) for x in secs}
    prev = client.get(f"/knowledge/jobs/{job['job_id']}/sections/vi/sec_5_7").json()
    assert prev["parent"]["section_key"] == "sec_5_7" and prev["children"]
    assert search_counts(s) == {"documents": 0, "points": 0}

    # ---- phát hành
    r = client.post(f"/knowledge/jobs/{job['job_id']}/publish", json={"user_id": "kysu_b"})
    assert r.json()["status"] == "PUBLISHING"
    run_worker(s, store)
    j = client.get(f"/knowledge/jobs/{job['job_id']}").json()
    assert (j["status"], j["published_by"]) == ("PUBLISHED", "kysu_b") and j["published_at"]
    doc_a = j["doc_id"]
    c = search_counts(s, doc_a)
    assert c["documents"] == 1 and c["points"] > 0 and qdrant_latest(s, doc_a) == {True}
    with pg_connect(s) as conn:
        assert conn.execute("SELECT count(*) AS n FROM parent_chunks WHERE doc_id = %s", (doc_a,)).fetchone()["n"] > 0

    # ---- Query Pipeline thấy tài liệu mới ngay (không khởi động lại; cache hồ sơ được làm mới)
    assert doc_a in load_profiles(deps, [PROJECT])[PROJECT]["docs"]
    out = run_turn(build_graph(deps, InMemorySaver()), "ai_do", QUESTION)
    assert out["status"] == "OK" and any(b["doc_id"] == doc_a for b in out["context"])
    assert client.get(f"/knowledge/documents/{doc_a}/file").content == SPEC_PDF.read_bytes()

    # ---- revision mới của bộ → bản cũ hết hiệu lực (Postgres + Qdrant)
    r = upload(client, variant_pdf(tmp_path, "revB"), revision="B", issue_date="2026-11-01",
               series={"mode": "revision", "doc_code": BASE_CODE})
    job_b = r.json()
    assert job_b["series_doc_code"] == BASE_CODE
    run_worker(s, store)
    assert client.get(f"/knowledge/jobs/{job_b['job_id']}").json()["replaces"]["doc_id"] == doc_a
    client.post(f"/knowledge/jobs/{job_b['job_id']}/publish", json={"user_id": "kysu_b"})
    run_worker(s, store)
    doc_b = client.get(f"/knowledge/jobs/{job_b['job_id']}").json()["doc_id"]
    assert (pg_latest(s, doc_a), qdrant_latest(s, doc_a)) == (False, {False})
    assert (pg_latest(s, doc_b), qdrant_latest(s, doc_b)) == (True, {True})
    hist = client.get(f"/knowledge/series/{BASE_CODE}").json()
    assert [r["revision"] for r in hist["revisions"]] == ["B", "A"]
    assert hist["revisions"][0]["published_by"] == "kysu_b"
    assert [r["revision"] for r in client.get(f"/knowledge/series/{BASE_CODE}",
                                              params={"to": "2026-10-31"}).json()["revisions"]] == ["A"]

    # ---- tài liệu mới trùng mã bộ → hậu tố _2
    r = upload(client, variant_pdf(tmp_path, "new"))
    assert r.json()["series_doc_code"] == BASE_CODE + "_2"
    assert client.post(f"/knowledge/jobs/{r.json()['job_id']}/cancel", json={}).json()["status"] == "CANCELLED"

    # ---- đặt lại bản cũ làm hiệu lực
    assert client.post(f"/knowledge/series/{BASE_CODE}/latest", json={"doc_id": doc_a}).status_code == 200
    assert (pg_latest(s, doc_a), qdrant_latest(s, doc_a)) == (True, {True})
    assert (pg_latest(s, doc_b), qdrant_latest(s, doc_b)) == (False, {False})

    # ---- gỡ khỏi tìm kiếm → mọi bản false; cây kho hiện "đã gỡ"
    client.post(f"/knowledge/series/{BASE_CODE}/unpublish")
    assert qdrant_latest(s, doc_a) == qdrant_latest(s, doc_b) == {False}
    assert not pg_latest(s, doc_a) and not pg_latest(s, doc_b)
    tree = client.get("/knowledge/tree", params={"project_id": PROJECT}).json()
    ser = tree[0]["buildings"][0]["streams"][0]["disciplines"][0]["series"][0]
    assert ser["unpublished"] and ser["n_revisions"] == 2
    assert load_profiles(deps, [PROJECT])[PROJECT]["docs"] == {}

    # ---- xóa → không còn bản ghi
    for d in (doc_a, doc_b):
        assert client.delete(f"/knowledge/documents/{d}").status_code == 200
    assert search_counts(s) == {"documents": 0, "points": 0}
    assert client.get(f"/knowledge/series/{BASE_CODE}").status_code == 404


def test_huy_job_khong_vao_db_tim_kiem(env, tmp_path):
    s, client, _, store = env
    job = upload(client, SPEC_PDF).json()
    run_worker(s, store)
    j = client.get(f"/knowledge/jobs/{job['job_id']}").json()
    assert j["status"] == "DRAFT"
    r = client.post(f"/knowledge/jobs/{job['job_id']}/cancel", json={"user_id": "u"})
    assert r.json()["status"] == "CANCELLED"
    assert client.get(f"/knowledge/jobs/{job['job_id']}/sections").status_code == 409     # artifact đã xóa
    assert search_counts(s) == {"documents": 0, "points": 0}
    assert store.exists(job["storage_key"])                                                # giữ file gốc
    assert worker.run_once(s, store=store) is None
    assert upload(client, SPEC_PDF).status_code == 201                                     # upload lại được


def test_job_loi_thi_failed_va_chay_lai(env, monkeypatch):
    s, client, _, store = env
    job = upload(client, SPEC_PDF).json()

    def boom(*a, **k):
        raise RuntimeError("Docling hỏng")

    monkeypatch.setattr("app.services.ingestion.pipeline.process", boom)
    run_worker(s, store)
    j = client.get(f"/knowledge/jobs/{job['job_id']}").json()
    assert j["status"] == "FAILED" and "Docling hỏng" in j["error"]
    monkeypatch.undo()
    assert client.post(f"/knowledge/jobs/{job['job_id']}/retry", json={}).json()["status"] == "QUEUED"
    run_worker(s, store)
    assert client.get(f"/knowledge/jobs/{job['job_id']}").json()["status"] == "DRAFT"


def test_du_an_alias_moi_dung_ngay(env):
    s, client, deps, _ = env
    r = client.patch(f"/projects/{PROJECT}", json={"aliases": ["Đảo Ngọc Xanh", "Bãi Đất Đỏ"]})
    assert r.status_code == 200 and r.json()["aliases"] == ["Đảo Ngọc Xanh", "Bãi Đất Đỏ"]
    assert resolve(deps, "u", "set", ["Đảo Ngọc Xanh"], []).active_projects == [PROJECT]
    r = client.post("/projects", json={"project_id": "27DA01", "name": "Dự án Mới",
                                       "buildings": [{"building": "T1", "aliases": ["Tháp Một"]}]})
    assert r.status_code == 201
    assert client.post("/projects", json={"project_id": "27DA01", "name": "x"}).status_code == 409
    assert resolve(deps, "u", "set", ["Tháp Một"], []).active_projects == ["27DA01"]
    assert client.post("/projects/27DA01/buildings", json={"building": "T2"}).status_code == 201
    assert client.delete("/projects/27DA01/buildings/T2").status_code == 200
    assert {p["project_id"] for p in client.get("/projects").json()} == {PROJECT, "27DA01"}
    bad = upload(client, SPEC_PDF, project_id="27DA01", building="S5")
    assert bad.status_code == 404
