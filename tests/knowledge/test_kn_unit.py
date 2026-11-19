"""Unit – kho tri thức (không DB, không gọi mạng): doc_code, ghi đè metadata, trạng thái job, trùng file_hash,
access.mode, alias, FileStore, nháp, làm mới cache hồ sơ dự án."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

from app.services.knowledge.errors import KnowledgeError
from app.services.knowledge.jobs import (base_doc_code, check_transition, find_duplicate, next_doc_code,
                                         validate_metadata)
from tests.knowledge.helpers import BASE_CODE, SPEC_META, SPEC_PDF

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 1. quy tắc doc_code

def test_doc_code_tai_lieu_moi_trung_thi_them_hau_to():
    base = base_doc_code("26DA06", "S5", "DESIGN_CONSULTANT", ["ELECTRICAL", "ELV"], "SPEC")
    assert base == BASE_CODE
    assert next_doc_code(base, set()) == base
    assert next_doc_code(base, {base}) == base + "_2"
    assert next_doc_code(base, {base, base + "_2"}) == base + "_3"
    assert next_doc_code(base, {base + "_2"}) == base   # chỉ hậu tố còn → mã gốc trống


def test_doc_code_revision_moi_dung_ma_cua_bo():
    md = validate_metadata({**SPEC_META, "series": {"mode": "revision", "doc_code": BASE_CODE + "_2"}},
                           doc_types=["SPEC"])
    assert md["series"] == {"mode": "revision", "doc_code": BASE_CODE + "_2"}
    with pytest.raises(KnowledgeError):
        validate_metadata({**SPEC_META, "series": {"mode": "revision"}}, doc_types=["SPEC"])


def test_validate_metadata():
    md = validate_metadata(SPEC_META, doc_types=["SPEC"])
    assert md["issue_date"] == date(2026, 10, 20) and md["stage"] is None and md["disciplines"] == ["ELECTRICAL"]
    for bad in ({"stream": "X"}, {"stream": ""}, {"project_id": ""}, {"disciplines": ["NOPE"]}, {"doc_type": "MEMO"},
                {"issue_date": "20/10/2026"}):
        with pytest.raises(KnowledgeError):
            validate_metadata({**SPEC_META, **bad}, doc_types=["SPEC"])


def test_upload_gon_chi_can_du_an_va_luong():
    """Form mới: công trình / loại tài liệu / phân hệ để trống → worker nhận diện; mã bộ = dựán_luồng[_n]."""
    md = validate_metadata({"project_id": "26DA06", "stream": "DESIGN_CONSULTANT"}, doc_types=["SPEC"])
    assert (md["building"], md["doc_type"], md["disciplines"]) == (None, None, [])
    assert md["series"] == {"mode": "new", "doc_code": None}
    base = base_doc_code("26DA06", None, "DESIGN_CONSULTANT", [], None)
    assert base == "26DA06_DESIGN_CONSULTANT" and next_doc_code(base, {base}) == base + "_2"
    # đủ thông tin (CLI / form cũ) → mã như cũ
    assert base_doc_code("26DA06", "S5", "DESIGN_CONSULTANT", ["ELECTRICAL"], "SPEC") == BASE_CODE


def test_ma_du_an_tu_sinh_tu_ten():
    from app.services.knowledge.projects import slug_id

    assert slug_id("Quảng Trường Biển (88ha)", set()) == "QUANG-TRUONG-BIEN-88HA"
    assert slug_id("Đại An", {"DAI-AN"}) == "DAI-AN-2"
    assert slug_id("Đại An", {"dai-an", "DAI-AN-2"}) == "DAI-AN-3"
    assert slug_id("!!!", set()) == "DA"
    assert len(slug_id("x" * 80, set())) <= 40


def test_nhan_dien_loai_tai_lieu_va_phan_he():
    """LLM trả JSON hợp lệ → dùng; lạ / lỗi → dự phòng (loại theo luồng, phân hệ GENERAL – luôn tìm thấy được)."""
    from types import SimpleNamespace

    from app.services.knowledge.classify import classify, parse_answer

    got = parse_answer('```json\n{"doc_type": "spec", "disciplines": ["ELECTRICAL", "elv", "XYZ"]}\n```',
                       "DESIGN_CONSULTANT")
    assert got == {"doc_type": "SPEC", "disciplines": ["ELECTRICAL", "ELV"], "source": "llm"}
    assert parse_answer("không biết", "OPERATOR_STANDARD") == {
        "doc_type": "BRAND_STANDARD", "disciplines": ["GENERAL"], "source": "fallback"}
    assert parse_answer('{"doc_type": "MEMO", "disciplines": ["HVAC"]}', "LEGAL_STANDARD")["doc_type"] == \
        "LEGAL_STANDARD"

    blocks = [SimpleNamespace(page=1, kind="text", text="CHỈ DẪN KỸ THUẬT PCCC", rows=None),
              SimpleNamespace(page=9, kind="text", text="trang sau – bỏ", rows=None)]

    class LLM:
        def __init__(self, reply):
            self.reply, self.seen = reply, None

        def invoke(self, msgs):
            self.seen = msgs[-1].content
            if isinstance(self.reply, Exception):
                raise self.reply
            return SimpleNamespace(content=self.reply)

    ok = LLM([{"type": "text", "text": '{"doc_type": "TECH_INSTRUCTION", "disciplines": ["FIRE"]}'}])
    assert classify(blocks, "S5_CHI DAN.pdf", "DESIGN_CONSULTANT", ok)["doc_type"] == "TECH_INSTRUCTION"
    assert "PCCC" in ok.seen and "trang sau" not in ok.seen
    assert classify(blocks, "x.pdf", "CONTRACT", LLM(RuntimeError("402")))["source"] == "fallback"
    assert classify([], "x.pdf", "CONTRACT", ok)["doc_type"] == "CONTRACT"      # PDF không có chữ → dự phòng


# ---------------------------------------------------------------- 2. ghi đè metadata

def _spec_path(tmp_path: Path) -> Path:
    return tmp_path / "2_Yeu cau ky thuat (Spec)" / "Spec_Dien_20261020.pdf"


def test_ghi_de_metadata(tmp_path, settings):
    from app.services.ingestion.metadata import infer_metadata, meta_summary

    path = _spec_path(tmp_path)
    base = infer_metadata(path, "a" * 64, [], settings=settings)
    assert base == infer_metadata(path, "a" * 64, [], settings=settings, overrides=None)   # CLI không đổi
    assert (base.stream, base.doc_type, base.disciplines) == ("DESIGN_CONSULTANT", "SPEC", ["ELECTRICAL"])
    assert base.issue_date == date(2026, 10, 20)

    ov = infer_metadata(path, "a" * 64, [], settings=settings, overrides={
        "building": "S5", "stream": "OPERATOR_STANDARD", "disciplines": ["HVAC"], "revision": "C",
        "issue_date": "2026-11-01", "title": "", "stage": None})
    assert (ov.project_id, ov.building, ov.stream, ov.disciplines) == ("26DA06", "S5", "OPERATOR_STANDARD", ["HVAC"])
    assert ov.doc_type == "SPEC"                              # không ghi đè → suy từ thư mục
    assert ov.title == base.title and ov.stage == base.stage  # để trống → tự suy
    assert (ov.revision, ov.issue_date) == ("C", date(2026, 11, 1))
    assert ov.doc_code == "26DA06_S5_OPERATOR_STANDARD_HVAC_SPEC"
    assert not any("building" in w or "revision" in w for w in ov.warnings)

    code = infer_metadata(path, "a" * 64, [], settings=settings, overrides={"doc_code": BASE_CODE + "_2"})
    assert code.doc_code == BASE_CODE + "_2"
    summ = meta_summary(code)
    assert summ["doc_code"] == BASE_CODE + "_2" and "doc_code_override" not in summ
    with pytest.raises(ValueError):
        infer_metadata(path, "a" * 64, [], settings=settings, overrides={"color": "red"})


def test_ghi_de_ngoai_folder_map_khong_canh_bao(tmp_path, settings):
    from app.services.ingestion.metadata import infer_metadata

    path = tmp_path / "uploads" / "26DA06" / ("b" * 64 + ".pdf")
    m = infer_metadata(path, "b" * 64, [], settings=settings, overrides={
        "project_id": "26DA06", "building": "S5", "stream": "DESIGN_CONSULTANT", "doc_type": "SPEC",
        "disciplines": ["ELECTRICAL"]})
    assert m.doc_code == BASE_CODE
    assert not any("folder_map" in w or "project_id" in w for w in m.warnings)


@pytest.fixture(scope="module")
def spec_result():
    """process() trên trang trích Spec Điện với ghi đè (Docling local có cache, LLM mock)."""
    import dataclasses

    from app.core.config import get_settings
    from app.providers.llm import MockChatModel
    from app.services.ingestion.pipeline import process

    s = dataclasses.replace(get_settings(), mock=frozenset({"all"}))
    steps: list[str] = []
    res = process(SPEC_PDF, settings=s, llm=MockChatModel(responses=["không phải JSON"]), on_step=steps.append,
                  overrides={"project_id": "26DA06", "building": "S5", "stream": "DESIGN_CONSULTANT",
                             "doc_type": "SPEC", "disciplines": ["ELECTRICAL"], "revision": "B",
                             "doc_code": BASE_CODE + "_2"})
    return res, steps


def test_process_ghi_de_va_bao_buoc(spec_result):
    res, steps = spec_result
    assert steps[:2] == ["parse", "bilingual"] and set(steps[2:]) == {"chunk_vi", "chunk_en"}
    d = res.document
    assert (d.doc_code, d.revision, d.building, d.stream) == (BASE_CODE + "_2", "B", "S5", "DESIGN_CONSULTANT")
    assert res.report["doc_code"] == BASE_CODE + "_2"
    assert all(c.project_id == "26DA06" and c.revision == "B" for c in res.children)


def test_draft_ghi_va_doc_lai(spec_result, tmp_path):
    from app.services.knowledge import drafts

    res, _ = spec_result
    art = drafts.save_draft(res, tmp_path / "job_x")
    assert art == tmp_path / "job_x" / res.meta.doc_id
    d = drafts.load_draft(art)
    assert d.document.doc_code == res.document.doc_code and len(d.parents) == len(res.parents)
    assert [c.child_id for c in d.children] == [c.child_id for c in res.children]
    secs = drafts.sections(art)
    assert {("vi", "sec_5_7"), ("en", "sec_5_7")} <= {(x["lang"], x["section_key"]) for x in secs}
    prev = drafts.section_preview(art, "vi", "sec_5_7")
    assert prev["parent"]["section_key"] == "sec_5_7" and prev["children"]
    assert drafts.section_preview(art, "vi", "khong_co") is None


# ---------------------------------------------------------------- 3. trạng thái job, trùng file_hash

def test_chuyen_trang_thai():
    assert check_transition("DRAFT", "publish") == "PUBLISHING"
    assert check_transition("FAILED", "retry") == "QUEUED"
    for st in ("QUEUED", "PROCESSING", "DRAFT", "PUBLISHING", "FAILED"):
        assert check_transition(st, "cancel") == "CANCELLED"
    for st, action in (("QUEUED", "publish"), ("PROCESSING", "publish"), ("PUBLISHED", "publish"),
                       ("PUBLISHED", "cancel"), ("CANCELLED", "cancel"), ("DRAFT", "retry"),
                       ("CANCELLED", "retry")):
        with pytest.raises(KnowledgeError) as e:
            check_transition(st, action)
        assert e.value.status == 409


class _Conn:
    """Conn giả: trả dòng theo bảng trong câu SQL."""

    def __init__(self, docs=None, jobs=None):
        self.docs, self.jobs = docs, jobs

    def execute(self, sql, args=None):
        row = self.docs if "FROM documents" in sql else self.jobs
        return type("R", (), {"fetchone": lambda _self: row})()


def test_trung_file_hash_bi_tu_choi():
    assert find_duplicate(_Conn(), "h") is None
    assert "doc_1" in find_duplicate(_Conn(docs={"doc_id": "doc_1", "doc_code": "X"}), "h")
    assert "job_1" in find_duplicate(_Conn(jobs={"job_id": "job_1", "status": "DRAFT"}), "h")


# ---------------------------------------------------------------- 6. access.mode

class _Pool:
    def __init__(self, rows):
        self.rows = rows

    def connection(self):
        rows = self.rows

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                return list(rows)

        return _C()


def test_access_mode(settings, monkeypatch):
    from app.repositories.postgres import PgRepo, access_mode

    pool = _Pool([{"project_id": "26DA06"}, {"project_id": "P2"}])
    assert PgRepo(settings, pool, mode="open").user_projects("ai_cung_duoc") == ["26DA06", "P2"]
    assert PgRepo(settings, pool, mode="config").user_projects("test_engineer") == ["26DA06"]
    assert PgRepo(settings, pool, mode="config").user_projects("la") == []
    monkeypatch.delenv("MEP_ACCESS_MODE", raising=False)
    assert access_mode() == "open"                    # config/knowledge.yaml
    monkeypatch.setenv("MEP_ACCESS_MODE", "config")
    assert access_mode() == "config" and PgRepo(settings, pool).mode == "config"
    monkeypatch.setenv("MEP_ACCESS_MODE", "khac")
    with pytest.raises(ValueError):
        access_mode()


# ---------------------------------------------------------------- 7. alias

def test_alias_tu_du_lieu_du_an():
    from app.db.migrate import project_alias_rows

    rows = project_alias_rows([{"project_id": "P1", "name": "Dự án Một", "aliases": ["Khu A"],
                                "owner_aliases": ["CĐT X"], "buildings": [{"building": "T1", "aliases": ["Tòa T1"]}]}])
    got = {(r[0], r[3], r[4]) for r in rows}
    assert {("p1", "", "project"), ("khua", "", "project"), ("t1", "T1", "building"),
            ("toat1", "T1", "building")} <= got
    assert any(r[4] == "owner" for r in rows)


# ---------------------------------------------------------------- FileStore

def test_file_store_va_upload(tmp_path):
    from app.services.knowledge.storage import LocalFileStore, is_pdf, spool_upload

    tmp, h, size = spool_upload(io.BytesIO(SPEC_PDF.read_bytes()), 50 * 1024 * 1024)
    try:
        assert size == SPEC_PDF.stat().st_size and len(h) == 64 and is_pdf(tmp)
        st = LocalFileStore(tmp_path)
        key = st.put("26DA06", h, tmp)
        assert key == f"26DA06/{h}.pdf" and st.exists(key)
        assert st.local_path(key) == tmp_path / "26DA06" / f"{h}.pdf"
    finally:
        tmp.unlink(missing_ok=True)
    with pytest.raises(ValueError):
        spool_upload(io.BytesIO(b"x" * 2048), 1024)
    with pytest.raises(ValueError):
        LocalFileStore(tmp_path).local_path("../../etc/passwd")


# ---------------------------------------------------------------- cache hồ sơ dự án

def test_cache_lam_moi_khi_phien_ban_kho_doi(mock_settings):
    from app.services.knowledge.cache import attach_store_version
    from app.services.query.deps import Deps

    deps = Deps(settings=mock_settings, repo=None, retriever=None, reranker=None, llm_fast=None, llm_main=None)
    version = {"v": 1}
    attach_store_version(deps, lambda: version["v"])
    calls = []

    def loader():
        calls.append(1)
        return {"n": len(calls)}

    assert deps.cached_profile("P", loader) == {"n": 1}
    assert deps.cached_profile("P", loader) == {"n": 1}       # cùng phiên bản → dùng cache (TTL)
    version["v"] = 2
    assert deps.cached_profile("P", loader) == {"n": 2}       # worker phát hành → nạp lại ngay
