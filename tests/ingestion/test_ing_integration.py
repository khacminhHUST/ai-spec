"""Integration: ingest toàn bộ bộ S5 vào namespace tạm (Postgres + Qdrant thật, embedding/LLM theo .env)
→ 10 tiêu chí nghiệm thu (docs/ingestion.md) + idempotent + versioning + dry-run.

Chạy: python -m pytest -m integration tests/ingestion -s
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
from qdrant_client import models as qm

from ingestion.pipeline import ingest_file, iter_inputs
from ingestion.store import counts
from shared.db import pg_connect, qdrant

pytestmark = pytest.mark.integration

WESTIN_CHAPTERS = ["GR1", "GR2", "GR3", "GR4", "1", "2A", "2B", "3", "4A", "4B", "4C", "4D", "5", "6", "7A", "7B",
                   "7C", "8A", "8B", "9", "10", "11A", "11B", "12", "13A", "13B", "14", "15A", "15B", "15C"]


@pytest.fixture(scope="module")
def ns(settings):
    from shared.migrate import drop, migrate

    s = settings.with_ns(f"t_ing_{uuid.uuid4().hex[:6]}")
    migrate(s)
    yield s
    drop(s)


@pytest.fixture(scope="module")
def s5(ns, s5_root, tmp_path_factory):
    out = tmp_path_factory.mktemp("artifacts")
    files = iter_inputs(s5_root)
    assert len(files) == 4
    res = {}
    for f in files:
        r = ingest_file(f, settings=ns, out_root=out)
        assert r["status"] == "inserted", r
        key = f.parent.name.split("_", 1)[0]  # 1..4
        res[key] = {**r, "report": json.loads((Path(r["artifacts"]) / "report.json").read_text(encoding="utf-8"))}
    return res


def _keys(ns, doc_id, lang=None, typ=None) -> list[str]:
    q = "SELECT section_key FROM parent_chunks WHERE doc_id = %s"
    args: list = [doc_id]
    if lang:
        q += " AND lang = %s"
        args.append(lang)
    if typ:
        q += " AND type = %s"
        args.append(typ)
    with pg_connect(ns) as conn:
        return [r["section_key"] for r in conn.execute(q, args)]


def _children(ns, lang, **match) -> list[dict]:
    flt = qm.Filter(must=[qm.FieldCondition(key=k, match=qm.MatchValue(value=v)) for k, v in match.items()])
    pts, off = [], None
    while True:
        batch, off = qdrant(ns).scroll(ns.collection(lang), scroll_filter=flt, limit=256, offset=off, with_payload=True)
        pts += [p.payload for p in batch]
        if off is None:
            return pts


# ---------------------------------------------------------------- 10 tiêu chí

def test_1_spec_song_ngu_du_muc_va_bang_ban_kinh_uon(ns, s5):
    doc = s5["2"]["doc_id"]
    vi, en = set(_keys(ns, doc, "vi", "SECTION")), set(_keys(ns, doc, "en", "SECTION"))
    need = {f"sec_5_{i}" for i in range(1, 25)}
    assert need <= vi and need <= en and vi == en
    with pg_connect(ns) as conn:
        rows = conn.execute("SELECT * FROM parent_chunks WHERE doc_id=%s AND type='TABLE' AND parent_section_key='sec_5_7'",
                            (doc,)).fetchall()
    bend = {r["lang"]: r for r in rows if "3 lần đường kính" in r["content_md"] or "3 times outside" in r["content_md"]}
    assert set(bend) == {"vi", "en"} and bend["vi"]["section_key"] == bend["en"]["section_key"]
    kv = _children(ns, "vi", parent_id=bend["vi"]["parent_id"])
    ke = _children(ns, "en", parent_id=bend["en"]["parent_id"])
    assert any("nhỏ hơn 10mm2" in c["text"] and "3 lần đường kính" in c["text"] for c in kv)
    assert any("3 times outside diameter" in c["text"] for c in ke)


def test_2_thuyet_minh_bang_4_3(ns, s5):
    doc = s5["1"]["doc_id"]
    with pg_connect(ns) as conn:
        t = conn.execute("SELECT * FROM parent_chunks WHERE doc_id=%s AND section_key='table_4_3'", (doc,)).fetchone()
        sec = conn.execute("SELECT referenced_ids FROM parent_chunks WHERE doc_id=%s AND section_key=%s",
                           (doc, t["parent_section_key"])).fetchone()
    header = t["content_md"].split("\n")[2]
    assert header.count("|") - 1 == 7 and "Function" in header      # 7 cột
    kids = _children(ns, "vi", parent_id=t["parent_id"])
    hotel = [c for c in kids if c.get("topic") == "Hotel Room" or ("Hotel Room" in c["raw_text"] and "Guest" in c["raw_text"])]
    assert hotel and any("| Guest |" in c["raw_text"] for c in hotel)
    assert "TCVN 5687:2024" in sec["referenced_ids"]


def test_3_pccc_13_chuong_chuong_2_du_muc(ns, s5):
    keys = set(_keys(ns, s5["3"]["doc_id"], "vi", "SECTION"))
    assert {f"chap_{i}" for i in range(1, 14)} <= keys
    assert {f"chap_2__sec_{i}" for i in range(1, 12)} <= keys


def test_4_westin_du_phan(ns, s5):
    keys = set(_keys(ns, s5["4"]["doc_id"], "en", "SECTION"))
    missing = [c for c in WESTIN_CHAPTERS if f"sec_{c.lower()}" not in keys]
    assert not missing
    assert {"sec_9_7", "sec_10_7", "sec_15c_6"} <= keys


def test_5_toan_ven_100_phan_tram(s5):
    for k, r in s5.items():
        for lang, x in r["report"]["langs"].items():
            assert not x["integrity_fail"], (k, lang, x["integrity_fail"][:5])
            assert x["integrity_pass"] == x["n_parents"]


def test_6_khong_child_rong_khong_vuot_token(s5):
    for k, r in s5.items():
        for lang, x in r["report"]["langs"].items():
            assert not x["empty_children"]
            assert all(o["single_table_row"] for o in x["oversized_children"]), (k, lang, x["oversized_children"])


def test_7_khong_cat_giua_so_thap_phan_ma_tieu_chuan(s5):
    for k, r in s5.items():
        for lang, x in r["report"]["langs"].items():
            assert not x["boundary_violations"], (k, lang, x["boundary_violations"][:5])


def test_8_idempotent(ns, s5, s5_root, tmp_path):
    before = counts(ns)
    for f in iter_inputs(s5_root):
        assert ingest_file(f, settings=ns, out_root=tmp_path)["status"] == "skipped"
    assert counts(ns) == before
    # --force ghi lại cùng ID → số bản ghi vẫn không đổi
    spec = next(f for f in iter_inputs(s5_root) if f.parent.name.startswith("2_"))
    assert ingest_file(spec, settings=ns, out_root=tmp_path, force=True)["status"] == "inserted"
    assert counts(ns) == before


def test_10_trang_dung_voi_pdf_goc(s5):
    total = ok = 0
    for r in s5.values():
        pc = r["report"]["page_check"]
        total += pc["sampled"]
        ok += pc["ok"]
    assert total >= 20 and ok == total


def test_payload_qdrant_dung_schema(ns, s5):
    kids = _children(ns, "vi", doc_id=s5["2"]["doc_id"])
    c = kids[0]
    for f in ("child_id", "parent_id", "section_key", "lang", "type", "text", "raw_text", "page_start", "project_id",
              "building", "stream", "disciplines", "doc_id", "revision", "is_latest"):
        assert f in c
    assert c["project_id"] == "26DA06" and c["building"] == "S5" and c["is_latest"] is True
    assert c["text"].startswith("[Quảng Trường Biển 88ha S5 | DESIGN_CONSULTANT | ELECTRICAL |")


# ---------------------------------------------------------------- 9. versioning + dry-run

@pytest.fixture
def ns2(settings):
    from shared.migrate import drop, migrate

    s = settings.with_ns(f"t_ingv_{uuid.uuid4().hex[:6]}")
    migrate(s)
    yield s
    drop(s)


def _copy(src: Path, dst_dir: Path, name: str, extra: bytes = b"") -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / name
    shutil.copyfile(src, dst)
    if extra:
        with dst.open("ab") as f:
            f.write(extra)
    return dst


def test_9_versioning_revision_moi(ns2, fixtures_dir, tmp_path):
    folder = tmp_path / "2_Yeu cau ky thuat (Spec)"
    old = _copy(fixtures_dir / "spec_p21-22.pdf", folder, "BDD_S5_E5_SPEC_01102026_Rev01.pdf")
    new = _copy(fixtures_dir / "spec_p21-22.pdf", folder, "BDD_S5_E5_SPEC_01112026_Rev02.pdf", b"\n% rev02\n")
    r1 = ingest_file(old, project="26DA06", building="S5", settings=ns2, out_root=tmp_path / "a")
    r2 = ingest_file(new, project="26DA06", building="S5", settings=ns2, out_root=tmp_path / "a")
    assert r1["status"] == r2["status"] == "inserted" and r1["doc_code"] == r2["doc_code"]
    with pg_connect(ns2) as conn:
        latest = {r["doc_id"]: (r["is_latest"], r["revision"]) for r in conn.execute("SELECT * FROM documents")}
    assert latest == {r1["doc_id"]: (False, "01"), r2["doc_id"]: (True, "02")}
    for lang in ("vi", "en"):
        assert {c["is_latest"] for c in _children(ns2, lang, doc_id=r1["doc_id"])} == {False}
        assert {c["is_latest"] for c in _children(ns2, lang, doc_id=r2["doc_id"])} == {True}
    # ingest lại bản cũ → bỏ qua, không đổi trạng thái
    assert ingest_file(old, project="26DA06", building="S5", settings=ns2, out_root=tmp_path / "a")["status"] == "skipped"


def test_dry_run_sinh_artifact_khong_ghi_db(ns2, fixtures_dir, tmp_path):
    f = _copy(fixtures_dir / "tm_p10-11.pdf", tmp_path / "1_Thuyet minh ky thuat", "20261017-S5-Report-VN.pdf")
    before = counts(ns2)
    r = ingest_file(f, project="26DA06", building="S5", settings=ns2, out_root=tmp_path / "art", dry_run=True)
    assert r["status"] == "dry_run"
    art = Path(r["artifacts"])
    for name in ("parsed.json", "vi.md", "parents.jsonl", "children.jsonl", "report.json"):
        assert (art / name).exists() and (art / name).stat().st_size > 0, name
    assert counts(ns2) == before
