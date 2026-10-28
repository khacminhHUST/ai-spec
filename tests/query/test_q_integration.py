"""Integration – Postgres + Qdrant thật (namespace tạm), LLM mock. Seed: tests/fixtures/query_seed."""
import dataclasses

import pytest

from shared.db import pg_pool
from shared.keys import normalize_alias
from shared.reranker import MockReranker

from query.deps import Deps
from query.graph import build_graph, postgres_checkpointer, run_turn
from query.repo import PgRepo
from query.resolver import build_profile
from query.retrieval import plan_branches
from query.retriever import QdrantRetriever
from tests.fixtures.query_seed.seed import add_aliases, load_seed, write_seed
from tests.query.fakes import approx_tokens, fake_project_doc, router_json, scripted_llm

pytestmark = pytest.mark.integration
S5 = "26DA06"
S6 = "TEST_S6"


@pytest.fixture
def seeded(test_ns):
    s = dataclasses.replace(test_ns, mock=frozenset({"all"}))   # embedding mock: không gọi mạng
    data = load_seed()
    data.extend(fake_project_doc(S6, "S6", lang="vi"))
    counts = write_seed(data, s)
    add_aliases(s, [(normalize_alias(a), a, S6, "S6", "building") for a in ("S6", "Tòa S6")])
    pool = pg_pool(s, min_size=1, max_size=4)
    yield s, data, counts, pool
    pool.close()


def _deps(s, pool, llm):
    return Deps(settings=s, repo=PgRepo(s, pool), retriever=QdrantRetriever(s), reranker=MockReranker(),
                llm_fast=llm, llm_main=llm, count_tokens=approx_tokens)


def test_seed_loaded(seeded):
    s, data, counts, pool = seeded
    assert counts == {"documents": 4, "parents": len(data.parents), "children": len(data.children)}
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM parent_chunks").fetchone()["n"] == len(data.parents)


def test_pg_repo(seeded):
    s, data, _, pool = seeded
    repo = PgRepo(s, pool)
    assert repo.user_projects("test_engineer") == [S5]
    assert {r["project_id"] for r in repo.aliases([S5, S6]) if r["alias_norm"] == "s6"} == {S6}
    rows = repo.parents_by_sections([("doc_bd42fa85440e", "sec_5_23")])
    assert {r["lang"] for r in rows} == {"vi", "en"}
    found = repo.find_sections(["doc_11833346273d"], ["table_4_2_1", "sec_4_2"])
    assert {r["section_key"] for r in found} == {"part_i__table_4_2_1", "part_i__sec_4_2"}
    assert repo.standards_in_system(["TCVN 5687:2024"]) == {}
    assert repo.project_info([S6])[S6]["name"] in ("S6", "Tòa S6")


def test_qdrant_retriever(seeded):
    s, _, _, _ = seeded
    r = QdrantRetriever(s)
    hits = r.search(project_id=S5, stream="DESIGN_CONSULTANT", lang="vi",
                    queries=["bán kính uốn cong tối thiểu cáp tiết diện lớn hơn 25mm2"], keywords=["25mm2"],
                    disciplines=["ELECTRICAL"])
    assert hits and {h["lang"] for h in hits} == {"vi"}
    assert "table_5_7_1" in [h["section_key"] for h in hits[:3]]
    assert "doc_efae3b1e6ca7" not in {h["doc_id"] for h in hits}   # FIRE bị lọc
    kids = r.children_of("doc_bd42fa85440e:vi:sec_5_7", "vi")
    assert [k["child_id"][-4:] for k in kids] == ["0001", "0002", "0003", "0004", "0005", "0006"]


def test_profile_branches_fake_project(seeded):
    s, _, _, pool = seeded
    deps = _deps(s, pool, scripted_llm(router_json()))
    profiles = {p: build_profile(deps, p) for p in (S5, S6)}
    assert profiles[S6]["streams"] == {"DESIGN_CONSULTANT": {"languages": ["vi"], "doc_ids": ["doc_faketest_s61"]}}
    br = plan_branches({"queries_vi": ["x"], "queries_en": ["x"], "streams": []}, profiles)
    assert {(b["project_id"], b["lang"]) for b in br} == {(S5, "vi"), (S5, "en"), (S6, "vi")}


def test_graph_with_postgres_checkpointer(seeded):
    s, _, _, pool = seeded
    gym = router_json(q="Gió tươi phòng gym S5", project_action="set", project_mentions=["S5"],
                      queries_vi=["gió tươi health club gym"])
    lounge = router_json(q="Gió tươi lounges S5", queries_vi=["gió tươi lounges"])
    deps = _deps(s, pool, scripted_llm([gym, lounge], answer="Theo Bảng 4.3 [1]."))
    with postgres_checkpointer(s) as cp:
        g = build_graph(deps, cp)
        t1 = run_turn(g, "test_engineer", "Gió tươi phòng gym S5?")
        assert t1["status"] == "OK" and t1["context"][0]["section_key"] == "table_4_3"
    with postgres_checkpointer(s) as cp:   # kết nối mới → state nạp lại từ Postgres
        g = build_graph(deps, cp)
        t2 = run_turn(g, "test_engineer", "Còn lounge thì sao?", t1["conversation_id"])
    assert t2["active_projects"] == [S5] and len(t2["messages"]) == 4
    conv = PgRepo(s, pool).list_conversations("test_engineer")
    assert [c["conversation_id"] for c in conv] == [t1["conversation_id"]]
