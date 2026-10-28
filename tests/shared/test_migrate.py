import pytest
from qdrant_client import models as qm

from shared.db import pg_connect, qdrant
from shared.embedding import get_embedder
from shared.keys import make_child_id, make_parent_id
from shared.migrate import migrate
from shared.schema import ChildChunk
from shared.sparse import doc_sparse, query_sparse

pytestmark = pytest.mark.integration


def test_tables_and_collections(test_ns):
    with pg_connect(test_ns) as conn:
        tables = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (test_ns.pg_schema,))}
        assert {"documents", "parent_chunks", "project_aliases", "conversations", "checkpoints"} <= tables
        n = conn.execute("SELECT count(*) AS n FROM project_aliases WHERE alias_norm = 'baidatdo'").fetchone()["n"]
        assert n == 1
    client = qdrant(test_ns)
    for lang in ("vi", "en"):
        info = client.get_collection(test_ns.collection(lang))
        assert info.config.params.vectors["dense"].size == int(test_ns.embedding_cfg(lang)["dimensions"])
        assert "sparse" in info.config.params.sparse_vectors
        assert {"project_id", "is_latest", "stream", "disciplines"} <= set(info.payload_schema)


def test_migrate_idempotent(test_ns):
    r = migrate(test_ns)
    assert r["postgres"] == [] and r["qdrant"] == []


def test_upsert_and_hybrid_query(test_ns, mock_settings):
    """Vòng đời tối thiểu: upsert child (mock embedding) → lọc + dense + sparse."""
    s = test_ns
    emb = get_embedder("vi", mock_settings)
    client = qdrant(s)
    texts = ["Bán kính uốn cong tối thiểu của cáp: 6 lần đường kính ngoài", "Thời gian bảo hành 24 tháng"]
    points = []
    for i, t in enumerate(texts):
        pid = make_parent_id("d1", "vi", f"sec_5_{i}")
        c = ChildChunk(child_id=make_child_id(pid, 0), parent_id=pid, section_key=f"sec_5_{i}", lang="vi",
                       type="TEXT", text=t, raw_text=t, doc_id="d1", project_id="26DA06",
                       stream="DESIGN_CONSULTANT", disciplines=["ELECTRICAL"])
        points.append(qm.PointStruct(id=c.point_id, payload=c.qdrant_payload(),
                                     vector={"dense": emb.embed_documents([t])[0],
                                             "sparse": qm.SparseVector(**doc_sparse(t))}))
    client.upsert(s.collection("vi"), points, wait=True)
    flt = qm.Filter(must=[qm.FieldCondition(key="project_id", match=qm.MatchValue(value="26DA06")),
                          qm.FieldCondition(key="is_latest", match=qm.MatchValue(value=True))])
    q = "bán kính uốn cáp"
    res = client.query_points(
        s.collection("vi"),
        prefetch=[qm.Prefetch(query=emb.embed_query(q), using="dense", filter=flt, limit=10),
                  qm.Prefetch(query=qm.SparseVector(**query_sparse(q)), using="sparse", filter=flt, limit=10)],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF), limit=2,
    ).points
    assert res[0].payload["section_key"] == "sec_5_0"
