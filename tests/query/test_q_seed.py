"""Unit – dữ liệu seed (tests/fixtures/query_seed) đúng schema + bộ câu hỏi vàng khớp seed."""
import hashlib
from pathlib import Path

import pytest
import yaml

from shared.keys import child_seq, make_parent_id
from tests.fixtures.query_seed.seed import load_seed

pytestmark = pytest.mark.unit
GOLDEN = Path(__file__).parents[1] / "fixtures" / "s5_golden.yaml"


@pytest.fixture(scope="module")
def data():
    return load_seed()


def test_ids_follow_convention(data, settings):
    folder = settings.project_list()[0]["folder_map"]
    for d in data.documents:
        assert d.doc_id == "doc_" + d.file_hash[:12]
        meta = next(v for k, v in folder.items() if f"/{k}/" in d.source_path)
        assert d.doc_code == f"{d.project_id}_{d.building}_{meta['stream']}_{meta['disciplines'][0]}_{meta['doc_type']}"
        assert d.stream == meta["stream"] and d.disciplines == meta["disciplines"]
        src = settings.data_root / d.source_path
        if src.exists():
            assert hashlib.sha256(src.read_bytes()).hexdigest() == d.file_hash
    for p in data.parents:
        assert p.parent_id == make_parent_id(p.doc_id, p.lang, p.section_key)
    seqs: dict[str, list[int]] = {}
    for c in data.children:
        assert c.text.startswith("[26DA06 S5 | ") and c.text.endswith(c.raw_text)
        seqs.setdefault(c.parent_id, []).append(child_seq(c.child_id))
        assert c.qdrant_payload()["child_id"] == c.child_id
    assert all(s == list(range(1, len(s) + 1)) for s in seqs.values())


def test_bilingual_spec_same_section_keys(data):
    spec = [p for p in data.parents if p.doc_id == "doc_bd42fa85440e"]
    assert {p.section_key for p in spec if p.lang == "vi"} == {p.section_key for p in spec if p.lang == "en"}


def test_seed_contains_golden_values(data):
    text = "\n".join(c.raw_text for c in data.children)
    for v in ["3 lần đường kính ngoài", "6 times outside diameter", "1.5mm2", "Pha A sơn màu vàng", "24 tháng",
              "2.5 L/s.person", "Min 2 ACH", "25 L/s/room", "5.6 W/m²·°K", "40o C", "0.9 L/s/m2"]:
        assert v in text, v


def test_golden_yaml_matches_seed(data):
    g = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    qs = {q["id"]: q for q in g["questions"]}
    assert sorted(qs) == list(range(1, 14))
    assert len(qs[13]["turns"]) == 2 and qs[12]["expected_status"] == "NOT_FOUND"
    codes = {d.doc_code: d.doc_id for d in data.documents}
    keys = {(p.doc_id, p.section_key) for p in data.parents}
    for q in qs.values():
        sec = q.get("expected_section")
        if not q["in_seed"] or not sec:
            continue
        seed_keys = sec.get("seed_section_keys") or [sec["section_key"]]  # seed gộp mục con khác bản ingest
        assert any((codes[sec["doc_code"]], k) in keys for k in seed_keys), q["id"]
