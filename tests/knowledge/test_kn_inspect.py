"""Integration: xem dữ liệu đã lưu của 1 revision (Postgres + Qdrant thật, namespace tạm, embedding mock)."""
from __future__ import annotations

from datetime import date

import pytest

from app.db.client import pg_connect, qdrant
from app.models.document import ParentChunk
from app.repositories.index_store import save_document
from app.services.knowledge import inspect
from app.services.knowledge.errors import KnowledgeError
from app.utils.keys import make_parent_id
from tests.knowledge.test_kn_db import make_doc

pytestmark = pytest.mark.integration


@pytest.fixture
def conn(kn_ns):
    with pg_connect(kn_ns, autocommit=True) as c:
        yield c


def test_xem_du_lieu_da_luu(conn, kn_ns):
    s = kn_ns
    doc, parents, children = make_doc("9", "A", date(2026, 1, 1))
    # thêm 1 bảng không có child → phải bị báo
    pid = make_parent_id(doc.doc_id, "vi", "table_4_3")
    parents.append(ParentChunk(parent_id=pid, doc_id=doc.doc_id, section_key="table_4_3", lang="vi", type="TABLE",
                               number="4.3", title="Gió tươi", content_md="| a | b |", page_start=3, page_end=3))
    save_document(doc, parents, children, settings=s)

    d = inspect.document_detail(conn, s, doc.doc_id)
    assert d["document"]["doc_code"] == doc.doc_code and "source_path" not in d["document"]
    (vi,) = d["langs"]
    assert (vi["n_parents"], vi["n_tables"], vi["n_children"]) == (2, 1, 1)
    assert vi["empty_parents"] == ["table_4_3"] and vi["orphan_children"] == 0 and vi["payload_mismatch"] == {}
    assert any("không có child" in c["message"] for c in d["checks"])

    # payload Qdrant lệch metadata → báo lỗi đồng bộ
    qdrant(s).set_payload(s.collection("vi"), payload={"revision": "Z"}, points=inspect._flt(doc.doc_id), wait=True)
    d = inspect.document_detail(conn, s, doc.doc_id)
    assert d["langs"][0]["payload_mismatch"] == {"revision": 1}

    secs = inspect.document_sections(conn, s, doc.doc_id)
    assert [(x["section_key"], x["n_children"]) for x in secs] == [("sec_1", 1), ("table_4_3", 0)]
    assert [x["section_key"] for x in inspect.document_sections(conn, s, doc.doc_id, q="revision A")] == ["sec_1"]

    one = inspect.document_section(conn, s, doc.doc_id, "vi", "sec_1")
    assert one["parent"]["title"] == "Gió tươi" and one["children"][0]["raw_text"].startswith("Lưu lượng")
    assert one["children"][0]["stream"] == "DESIGN_CONSULTANT"

    with pytest.raises(KnowledgeError):
        inspect.document_section(conn, s, doc.doc_id, "vi", "sec_99")
    with pytest.raises(KnowledgeError):
        inspect.document_detail(conn, s, "khong_co")


def test_sua_du_lieu_da_luu(conn, kn_ns):
    from app.services.knowledge import edits

    s = kn_ns
    doc, parents, children = make_doc("8", "A", date(2026, 1, 1))
    save_document(doc, parents, children, settings=s)
    did = doc.doc_id
    cid = children[0].child_id

    # sửa mục: Postgres, token_count tính lại
    out = edits.update_section(conn, s, did, "vi", "sec_1", {"title": "Gió tươi (sửa)", "content_md": "Nội dung mới"})
    assert out["parent"]["title"] == "Gió tươi (sửa)" and out["parent"]["token_count"] > 0

    # sửa child: text dựng lại (giữ breadcrumb), embed lại, point id giữ nguyên
    out = edits.update_child(conn, s, did, "vi", cid, {"raw_text": "Lưu lượng 30 m3/h/người", "topic": "Gió tươi"})
    (c,) = out["children"]
    assert c["child_id"] == cid and c["raw_text"] == "Lưu lượng 30 m3/h/người"
    assert c["text"].endswith("Lưu lượng 30 m3/h/người") and c["topic"] == "Gió tươi"
    assert c["stream"] == "DESIGN_CONSULTANT" and c["revision"] == "A"

    # thêm child: seq kế tiếp, payload lấy theo documents
    out = edits.add_child(conn, s, did, "vi", "sec_1", {"raw_text": "Đoạn bổ sung"})
    assert [x["child_id"].rsplit("#", 1)[1] for x in out["children"]] == ["0001", "0002"]
    new = out["children"][1]
    assert new["text"].startswith("[") and new["project_id"] == "26DA06" and new["is_latest"] is True
    assert inspect.document_detail(conn, s, did)["langs"][0]["payload_mismatch"] == {}

    # xóa child, rồi xóa cả mục
    out = edits.delete_child(conn, s, did, "vi", new["child_id"])
    assert len(out["children"]) == 1
    with pytest.raises(KnowledgeError):
        edits.update_child(conn, s, did, "vi", new["child_id"], {"raw_text": "x"})
    with pytest.raises(KnowledgeError):
        edits.update_child(conn, s, did, "vi", cid, {"raw_text": "  "})
    edits.delete_section(conn, s, did, "vi", "sec_1")
    d = inspect.document_detail(conn, s, did)
    assert d["langs"] == [] and any("Không có mục" in c["message"] for c in d["checks"])
