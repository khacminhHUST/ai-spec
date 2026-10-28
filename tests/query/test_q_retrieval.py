"""Unit N3 (sinh nhánh, nhánh lỗi/timeout) + N4 (rerank)."""
import time

import pytest

from app.services.query.resolver import build_profile
from app.services.query.retrieval import plan_branches, rerank, run_branch
from tests.fixtures.query_seed.seed import load_seed
from tests.query.fakes import S5, S6, FakeRetriever, fake_project_doc, make_deps

pytestmark = pytest.mark.unit


@pytest.fixture
def data():
    d = load_seed()
    d.extend(fake_project_doc(S6, "S6", lang="vi"))
    d.extend(fake_project_doc(S5, "S5", lang="en", stream="OPERATOR_STANDARD", doc_no="w"))
    return d


@pytest.fixture
def deps(mock_settings, data):
    return make_deps(mock_settings, data)


def _profiles(deps, pids):
    return {p: build_profile(deps, p) for p in pids}


def test_vi_only_project_has_no_en_branch(deps):
    router = {"queries_vi": ["gió tươi"], "queries_en": ["fresh air"], "streams": []}
    br = plan_branches(router, _profiles(deps, [S5, S6]))
    s6 = [b for b in br if b["project_id"] == S6]
    assert s6 == [{"project_id": S6, "stream": "DESIGN_CONSULTANT", "lang": "vi"}]


def test_empty_streams_means_all_streams_with_docs(deps):
    router = {"queries_vi": ["x"], "queries_en": ["x"], "streams": []}
    br = {(b["stream"], b["lang"]) for b in plan_branches(router, _profiles(deps, [S5]))}
    # S5: DESIGN_CONSULTANT vi+en, OPERATOR_STANDARD chỉ en; CONTRACT/LEGAL không có tài liệu → không sinh nhánh
    assert br == {("DESIGN_CONSULTANT", "vi"), ("DESIGN_CONSULTANT", "en"), ("OPERATOR_STANDARD", "en")}


def test_streams_filter_and_no_en_queries(deps):
    br = plan_branches({"queries_vi": ["x"], "queries_en": [], "streams": ["DESIGN_CONSULTANT"]}, _profiles(deps, [S5]))
    assert br == [{"project_id": S5, "stream": "DESIGN_CONSULTANT", "lang": "vi"}]
    # stream router chọn không có tài liệu → mở rộng ra mọi stream có tài liệu (tránh NOT_FOUND giả)
    br = plan_branches({"queries_vi": ["x"], "queries_en": [], "streams": ["CONTRACT"]}, _profiles(deps, [S5]))
    assert br and all(b["stream"] != "CONTRACT" for b in br)


def test_branch_filters_payload(deps):
    router = {"queries_vi": ["bán kính uốn cong cáp"], "exact_keywords": [], "disciplines": ["ELECTRICAL"]}
    hits = run_branch(deps, {"project_id": S5, "stream": "DESIGN_CONSULTANT", "lang": "vi"}, router)
    assert hits and all(h["lang"] == "vi" and h["project_id"] == S5 for h in hits)
    # discipline ELECTRICAL ∪ GENERAL: tài liệu PCCC (FIRE) bị loại
    assert "doc_efae3b1e6ca7" not in {h["doc_id"] for h in hits}


def test_branch_error_is_dropped(mock_settings, data):
    retr = FakeRetriever(data, fail_branches={(S5, "DESIGN_CONSULTANT", "vi")})
    deps = make_deps(mock_settings, data, retriever=retr)
    assert run_branch(deps, {"project_id": S5, "stream": "DESIGN_CONSULTANT", "lang": "vi"}, {"queries_vi": ["x"]}) == []


def test_branch_timeout_is_dropped(mock_settings, data):
    class Slow(FakeRetriever):
        def search(self, **kw):
            time.sleep(0.5)
            return super().search(**kw)

    deps = make_deps(mock_settings, data, retriever=Slow(data), pipeline_overrides={"retrieval": {"branch_timeout_s": 0.1}})
    t = time.monotonic()
    assert run_branch(deps, {"project_id": S5, "stream": "DESIGN_CONSULTANT", "lang": "vi"}, {"queries_vi": ["cáp"]}) == []
    assert time.monotonic() - t < 0.4


# ------------------------------------------------------------------ N4

class FixedReranker:
    def __init__(self, scores):
        self.scores = scores

    def score(self, query, documents):
        return [self.scores[d] for d in documents]


def _cand(i, doc):
    return {"child_id": f"{doc}:vi:sec_1#{i:04d}", "parent_id": f"{doc}:vi:sec_1", "doc_id": doc, "section_key": "sec_1",
            "lang": "vi", "text": f"t{doc}{i}"}


def test_rerank_threshold_and_per_doc_limit(deps):
    cands = [_cand(i, "docA") for i in range(8)] + [_cand(i, "docB") for i in range(3)]
    scores = {f"tdocA{i}": 0.9 - i * 0.05 for i in range(8)} | {"tdocB0": 0.31, "tdocB1": 0.29, "tdocB2": 0.1}
    deps.reranker = FixedReranker(scores)
    kept, top1 = rerank(deps, "q", cands)
    a = [h for h in kept if h["doc_id"] == "docA"]
    b = [h for h in kept if h["doc_id"] == "docB"]
    assert len(a) == 5 and [h["text"] for h in a] == [f"tdocA{i}" for i in range(5)]   # max_per_doc = 5
    assert [h["text"] for h in b] == ["tdocB0"]                                          # min_score = 0.3
    assert top1 == pytest.approx(0.9) and kept == sorted(kept, key=lambda h: -h["rerank_score"])


def test_rerank_empty(deps):
    deps.reranker = FixedReranker({"tdocA0": 0.1})
    assert rerank(deps, "q", [_cand(0, "docA")]) == ([], 0.0)
    assert rerank(deps, "q", []) == ([], 0.0)


def test_rerank_dedupes_child(deps):
    deps.reranker = FixedReranker({"tdocA0": 0.8})
    kept, _ = rerank(deps, "q", [_cand(0, "docA"), _cand(0, "docA")])
    assert len(kept) == 1
