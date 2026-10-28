"""Unit (trang trích từ bộ S5, Docling chạy local – có cache): tách song ngữ (3) + bảng thật (4)."""
from __future__ import annotations

import pytest

from app.services.ingestion.bilingual import detect_languages, postprocess, split_languages
from app.services.ingestion.chunks import table_children
from app.services.ingestion.parse import parse_pdf
from app.services.ingestion.structure import build_structure
from app.services.ingestion.textutil import detect_lang, profile

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def spec_split(fixtures_dir):
    prof = profile("SPEC")
    pd = parse_pdf(fixtures_dir / "spec_p21-22.pdf")
    li = detect_languages(pd, prof)
    warns: list[str] = []
    out = split_languages(pd, prof, li, warns)
    return li, {lg: postprocess(bl, prof, warns) for lg, bl in out.items()}


def test_spec_song_ngu_trai_en_phai_vi(spec_split):
    li, out = spec_split
    assert li["bilingual"] and (li["left"], li["right"]) == ("en", "vi")
    for lang, blocks in out.items():
        texts = [b for b in blocks if b.kind in ("text", "list") and len(b.text) > 40]
        assert texts
        ok = sum(1 for b in texts if detect_lang(b.text) == lang)
        assert ok / len(texts) >= 0.9, lang
        mid = 595 * 0.548
        assert all((b.bbox[0] + b.bbox[2]) / 2 < mid for b in texts) if lang == "en" else \
            all((b.bbox[0] + b.bbox[2]) / 2 > mid for b in texts)


def test_heading_song_ngu_giu_so_muc_ca_hai_ben(spec_split):
    _, out = spec_split
    heads = {lg: [b.text for b in bl if b.kind == "heading"] for lg, bl in out.items()}
    assert "5.7 WIRES AND CABLES" in heads["en"]
    assert "5.7 DÂY VÀ CÁP ĐIỆN" in heads["vi"]
    st = {lg: build_structure(bl, profile("SPEC")) for lg, bl in out.items()}
    assert [s.key for s in st["en"].sections] == [s.key for s in st["vi"].sections]
    assert "sec_5_7" in [s.key for s in st["vi"].sections]


def test_bang_4_cot_tach_thanh_2_bang_2_cot(spec_split):
    _, out = spec_split
    en = [b for b in out["en"] if b.kind == "table"]
    vi = [b for b in out["vi"] if b.kind == "table"]
    bend_en = next(t for t in en if any("3 times outside diameter" in c for r in t.rows for c in r))
    bend_vi = next(t for t in vi if any("3 lần đường kính" in c for r in t.rows for c in r))
    assert len(bend_en.rows[0]) == 2 and len(bend_vi.rows[0]) == 2
    # bảng ngắt trang: header ở trang 21, dữ liệu trang 22 → ghép
    assert bend_vi.rows[0] == ["Loại cáp", "Bán kính uốn cong tối thiểu của cáp"]
    assert bend_en.rows[0] == ["Cable type", "Minimum bend radius of cable"]
    assert (bend_vi.page, bend_vi.page_end) == (1, 2)
    assert any("nhỏ hơn 10mm2" in r[0] and "3 lần đường kính" in r[1] for r in bend_vi.rows)
    # không lẫn chữ Việt trong bảng EN và ngược lại
    assert all(detect_lang(" ".join(r)) != "vi" for r in bend_en.rows)


def test_bang_thuyet_minh_4_3_ghep_trang_va_nhom_hotel_room(fixtures_dir):
    from app.services.ingestion.chunks import build_parents
    from app.services.ingestion.metadata import DocMeta

    prof = profile("DESIGN_REPORT")
    pd = parse_pdf(fixtures_dir / "tm_p10-11.pdf")
    li = detect_languages(pd, prof)
    out = split_languages(pd, prof, li, [])
    blocks = postprocess(out["vi"], prof, [])
    st = build_structure(blocks, prof)
    t43 = next(t for t in st.tables if t.key == "table_4_3")
    rows = t43.block.rows
    assert t43.block.page_end == 2 and any(r[0] == "Lounges" for r in rows)
    assert len(rows[0]) == 7 and t43.block.header_rows == 2
    guest = next(r for r in rows if r[0] == "Guest")
    assert guest[1] == "10" and "2.5 L/s.person" in guest[4]
    meta = DocMeta("doc_fixture00001", "0" * 64, "x.pdf", project_id="26DA06", building="S5",
                   stream="DESIGN_CONSULTANT", disciplines=["HVAC"])
    d = next(x for x in build_parents(st, meta, "vi") if x.parent.section_key == "table_4_3")
    kids = table_children(d, "vi", 300, 40, [])
    hotel = [k for k in kids if k.topic == "Hotel Room"]
    assert hotel and "| Guest |" in hotel[0].raw
    assert all(k.prefix.startswith("Bảng 4.3") and "Function" in k.prefix for k in kids)
