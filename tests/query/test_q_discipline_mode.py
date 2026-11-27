"""Unit – lọc phân hệ hard/soft trong QdrantRetriever (client Qdrant giả, không gọi mạng)."""
from types import SimpleNamespace

import pytest

from app.repositories.qdrant import QdrantRetriever

pytestmark = pytest.mark.unit


class _Client:
    def __init__(self):
        self.kw = None

    def query_points_groups(self, collection, **kw):
        self.kw = kw
        return SimpleNamespace(groups=[])


class _Emb:
    def embed_query(self, text):
        return [0.1, 0.2]


def _disc(flt):
    return [c.match.any for c in flt.must if c.key == "disciplines"]


def _run(mode):
    s = SimpleNamespace(pipeline={"retrieval": {"top_k_per_doc": 2, "keyword_boost": 1.0, "discipline_mode": mode}},
                        collection=lambda lang: f"c_{lang}")
    client = _Client()
    r = QdrantRetriever(s, client=client, embedders={"vi": _Emb()})
    r.search(project_id="P", stream="DESIGN_CONSULTANT", lang="vi", queries=["báo cháy"], keywords=[],
             disciplines=["FIRE"])
    return client.kw


def test_hard_mode_filters_everything_by_discipline():
    kw = _run("hard")
    assert all(_disc(p.filter) == [["FIRE", "GENERAL"]] for p in kw["prefetch"])
    assert _disc(kw["query_filter"]) == [["FIRE", "GENERAL"]]


def test_soft_mode_adds_unfiltered_prefetch():
    kw = _run("soft")
    discs = [_disc(p.filter) for p in kw["prefetch"]]
    assert [["FIRE", "GENERAL"]] in discs and [] in discs           # có nhánh lọc + nhánh không lọc
    assert _disc(kw["query_filter"]) == []                           # không loại tài liệu gắn phân hệ khác


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        _run("xx")
