"""Integration (Postgres + Qdrant thật, namespace tạm, embedding mock) – không parse PDF:
seed dự án, sửa dự án/công trình → sinh lại alias, worker SKIP LOCKED + job dở dang, quản lý phiên bản."""
from __future__ import annotations

from datetime import date

import psycopg
import pytest
from qdrant_client import models as qm

from app.db.client import pg_connect, qdrant
from app.models.document import ChildChunk, Document, ParentChunk
from app.repositories.index_store import save_document
from app.services.knowledge import projects, versions
from app.services.knowledge.errors import KnowledgeError
from app.services.knowledge.worker import INTERRUPTED, claim_next, recover_interrupted
from app.utils.keys import make_child_id, make_parent_id

pytestmark = pytest.mark.integration
CODE = "26DA06_S5_DESIGN_CONSULTANT_HVAC_SPEC"


@pytest.fixture
def conn(kn_ns):
    with pg_connect(kn_ns, autocommit=True) as c:
        yield c


# ---------------------------------------------------------------- dự án / alias

def test_seed_du_an_tu_yaml(conn):
    p = projects.get_project(conn, "26DA06")
    assert p["buildings"] == [{"building": "S5", "aliases": ["S5", "Tòa S5", "Tòa nhà S5", "Lô S5"]}]
    assert p["source_priority"][0] == "CONTRACT" and p["governing_language"] == "vi"
    n = conn.execute("SELECT count(*) AS n FROM project_aliases WHERE alias_norm = 'baidatdo'").fetchone()["n"]
    assert n == 1


def _aliases(conn, pid):
    return {(r["alias_norm"], r["building"]) for r in
            conn.execute("SELECT alias_norm, building FROM project_aliases WHERE project_id = %s", (pid,))}


def test_sua_alias_sinh_lai_project_aliases(conn, kn_ns):
    from app.db.client import pg_pool
    from app.repositories.postgres import PgRepo

    projects.create_project(conn, {"project_id": "27DA01", "name": "Dự án Thử", "aliases": ["Khu Thử"],
                                   "buildings": [{"building": "T1", "aliases": ["Tòa T1"]}]})
    assert {("khuthu", ""), ("toat1", "T1"), ("27da01", "")} <= _aliases(conn, "27DA01")
    projects.update_project(conn, "27DA01", {"aliases": ["Khu Mới"]})
    al = _aliases(conn, "27DA01")
    assert ("khumoi", "") in al and ("khuthu", "") not in al
    projects.add_building(conn, "27DA01", {"building": "T2", "aliases": ["Tháp Hai"]})
    projects.update_building(conn, "27DA01", "T1", {"aliases": ["Khối Một"]})
    al = _aliases(conn, "27DA01")
    assert {("thaphai", "T2"), ("khoimot", "T1")} <= al and ("toat1", "T1") not in al
    projects.delete_building(conn, "27DA01", "T2")
    assert not any(b == "T2" for _, b in _aliases(conn, "27DA01"))
    assert ("baidatdo", "") in _aliases(conn, "26DA06")          # dự án khác không bị ảnh hưởng
    with pytest.raises(KnowledgeError):
        projects.create_project(conn, {"project_id": "27DA01", "name": "trùng"})
    with pytest.raises(KnowledgeError):
        projects.create_project(conn, {"project_id": "có_gạch", "name": "x"})
    # repo đọc DB: chế độ open thấy dự án mới, project_info đúng định dạng
    pool = pg_pool(kn_ns, min_size=1, max_size=2)
    try:
        repo = PgRepo(kn_ns, pool, mode="open")
        assert repo.user_projects("bat_ky") == ["26DA06", "27DA01"]
        assert PgRepo(kn_ns, pool, mode="config").user_projects("test_engineer") == ["26DA06"]
        info = repo.project_info(["27DA01"])["27DA01"]
        assert info == {"name": "Dự án Thử", "aliases": ["T1", "Khu Mới"], "source_priority": [],
                        "governing_language": "vi"}
    finally:
        pool.close()


# ---------------------------------------------------------------- worker

def _job(conn, job_id, status="QUEUED", step=None, h=None):
    conn.execute(
        "INSERT INTO ingest_jobs (job_id, file_name, file_hash, storage_key, project_id, building, stream, disciplines, "
        "doc_type, series_doc_code, status, step) VALUES (%s,'x.pdf',%s,'k','26DA06','S5','DESIGN_CONSULTANT',"
        "'{ELECTRICAL}','SPEC','C',%s,%s)", (job_id, h or job_id, status, step))


def test_worker_skip_locked(conn, kn_ns):
    _job(conn, "job_a")
    _job(conn, "job_b")
    with pg_connect(kn_ns) as other:   # worker khác đang giữ khóa job_a
        other.execute("SELECT 1 FROM ingest_jobs WHERE job_id = 'job_a' FOR UPDATE")
        got = claim_next(conn)
        assert got["job_id"] == "job_b" and got["status"] == "PROCESSING"
        assert claim_next(conn) is None          # job_a bị khóa → bỏ qua, không chờ
    assert claim_next(conn)["job_id"] == "job_a"
    # phát hành chờ worker được ưu tiên
    _job(conn, "job_c")
    _job(conn, "job_p", status="PUBLISHING", step="queued")
    assert claim_next(conn)["job_id"] == "job_p"


def test_worker_job_do_dang_thanh_failed(conn):
    _job(conn, "job_1", status="PROCESSING", step="chunk_vi")
    _job(conn, "job_2", status="PUBLISHING", step="embed")
    _job(conn, "job_3", status="PUBLISHING", step="queued")
    _job(conn, "job_4", status="DRAFT")
    assert recover_interrupted(conn) == 2
    st = {r["job_id"]: (r["status"], r["error"]) for r in conn.execute("SELECT job_id, status, error FROM ingest_jobs")}
    assert st["job_1"] == ("FAILED", INTERRUPTED) and st["job_2"][0] == "FAILED"
    assert st["job_3"][0] == "PUBLISHING" and st["job_4"][0] == "DRAFT"


def test_mot_file_mot_job_hieu_luc(conn):
    _job(conn, "job_x", h="hash1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _job(conn, "job_y", h="hash1")
    conn.execute("UPDATE ingest_jobs SET status = 'CANCELLED' WHERE job_id = 'job_x'")
    _job(conn, "job_y", h="hash1")


# ---------------------------------------------------------------- phiên bản

def make_doc(no: str, revision: str, issue: date) -> tuple[Document, list[ParentChunk], list[ChildChunk]]:
    doc_id = f"doc_ver{no:0>8}"
    doc = Document(doc_id=doc_id, doc_code=CODE, project_id="26DA06", building="S5", stream="DESIGN_CONSULTANT",
                   disciplines=["HVAC"], title="Spec HVAC thử", revision=revision, issue_date=issue, languages=["vi"],
                   source_path=f"fake/{doc_id}.pdf", file_hash=doc_id * 4)
    pid = make_parent_id(doc_id, "vi", "sec_1")
    text = f"Lưu lượng gió tươi revision {revision}"
    parent = ParentChunk(parent_id=pid, doc_id=doc_id, section_key="sec_1", lang="vi", type="SECTION", number="1",
                         title="Gió tươi", content_md=text, page_start=1, page_end=1)
    child = ChildChunk(child_id=make_child_id(pid, 1), parent_id=pid, section_key="sec_1", lang="vi", type="TEXT",
                       text=text, raw_text=text, project_id="26DA06", building="S5", stream="DESIGN_CONSULTANT",
                       disciplines=["HVAC"], doc_id=doc_id, revision=revision)
    return doc, [parent], [child]


def latest_state(s, conn) -> dict[str, tuple[bool, set[bool]]]:
    """doc_id → (is_latest Postgres, tập is_latest trên payload Qdrant)."""
    out = {}
    for r in conn.execute("SELECT doc_id, is_latest FROM documents WHERE doc_code = %s", (CODE,)):
        flt = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=r["doc_id"]))])
        pts, _ = qdrant(s).scroll(s.collection("vi"), scroll_filter=flt, limit=50, with_payload=True)
        out[r["doc_id"]] = (r["is_latest"], {p.payload["is_latest"] for p in pts})
    return out


def test_dat_hieu_luc_go_xoa(conn, kn_ns):
    s = kn_ns
    a, b, c = (make_doc("1", "A", date(2026, 1, 1)), make_doc("2", "B", date(2026, 5, 1)),
               make_doc("3", "C", date(2026, 9, 1)))
    for d in (a, b, c):
        save_document(*d, s, embed_cache=False)
    ids = [a[0].doc_id, b[0].doc_id, c[0].doc_id]
    assert latest_state(s, conn) == {ids[0]: (False, {False}), ids[1]: (False, {False}), ids[2]: (True, {True})}

    versions.set_latest(conn, s, CODE, ids[0])                       # quay lại bản cũ
    assert latest_state(s, conn) == {ids[0]: (True, {True}), ids[1]: (False, {False}), ids[2]: (False, {False})}

    hist = versions.series_history(conn, CODE)
    assert [r["revision"] for r in hist["revisions"]] == ["C", "B", "A"] and not hist["unpublished"]
    assert [r["revision"] for r in versions.series_history(conn, CODE, date(2026, 4, 1), date(2026, 12, 31))
            ["revisions"]] == ["C", "B"]

    versions.unpublish(conn, s, CODE)                                # gỡ khỏi tìm kiếm
    assert all(v == (False, {False}) for v in latest_state(s, conn).values())
    tree = versions.tree(conn, "26DA06")
    ser = tree[0]["buildings"][0]["streams"][0]["disciplines"][0]["series"][0]
    assert ser["doc_code"] == CODE and ser["unpublished"] and ser["n_revisions"] == 3

    versions.set_latest(conn, s, CODE, ids[2])                       # khôi phục
    versions.delete_revision(conn, s, ids[2])                        # xóa bản đang hiệu lực → B lên thay
    st = latest_state(s, conn)
    assert st == {ids[0]: (False, {False}), ids[1]: (True, {True})}
    n = qdrant(s).count(s.collection("vi"), count_filter=qm.Filter(must=[qm.FieldCondition(
        key="doc_id", match=qm.MatchValue(value=ids[2]))]), exact=True).count
    assert n == 0
    assert conn.execute("SELECT count(*) AS n FROM parent_chunks WHERE doc_id = %s", (ids[2],)).fetchone()["n"] == 0

    versions.delete_revision(conn, s, ids[0])                        # xóa bản không hiệu lực → B giữ nguyên
    assert latest_state(s, conn) == {ids[1]: (True, {True})}
    with pytest.raises(KnowledgeError):
        versions.set_latest(conn, s, CODE, ids[0])
