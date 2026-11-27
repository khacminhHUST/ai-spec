"""Unit: chuẩn hóa + chấm deterministic + gộp với judge (tests/eval/scoring.py)."""
from __future__ import annotations

import pytest

from tests.eval import scoring as S

pytestmark = pytest.mark.unit


def test_normalize():
    assert S.normalize("Dày **12 mm**, diện tích 2,5 m²") == "dày12mm,diệntích2.5m2"
    assert S.normalize("  ISO  8528 ") == "iso8528"
    assert S.normalize(None) == ""


def test_include_variants_and_ratio():
    ans = "Thảm rộng tối thiểu 750 mm, dày 12 mm, kéo dài ra ngoài tủ ít nhất 600mm."
    d = S.deterministic(ans, [["12mm", "12 mm"], "750mm", "600 MM"], [])
    assert d["include_ratio"] == 1.0 and d["missing"] == [] and d["must_not_violated"] == []
    d = S.deterministic(ans, ["12mm", "900mm"], None)
    assert d["include_ratio"] == 0.5 and d["missing"] == ["900mm"]


def test_number_boundaries():
    assert not S.contains(S.normalize("6000 mm"), "600mm")
    assert not S.contains(S.normalize("112mm"), "12mm")
    assert not S.contains(S.normalize("0.65"), "0,6")
    assert S.contains(S.normalize("hệ số 0,6;"), "0.6")
    assert S.contains(S.normalize("(12mm)"), "12mm")


def test_must_not_and_empty_include():
    d = S.deterministic("Lưu lượng 3.8 l/s", [], ["3.8"])
    assert d["include_ratio"] == 1.0 and d["must_not_violated"] == ["3.8"]
    assert S.combine(d, None)["pass"] is False


def test_combine_rules():
    full = {"include_ratio": 1.0, "must_not_violated": []}
    part = {"include_ratio": 0.67, "must_not_violated": []}
    low = {"include_ratio": 0.5, "must_not_violated": []}
    j = lambda v, h=False: {"verdict": v, "hallucination": h}  # noqa: E731
    assert S.combine(full, j("partial"))["pass"] is True          # rule a
    assert S.combine(full, j("wrong"))["pass"] is False
    assert S.combine(full, j("correct", True))["pass"] is False   # bịa → fail
    assert S.combine(part, j("correct"))["pass"] is True          # rule b
    assert S.combine(part, j("partial"))["pass"] is False
    assert S.combine(low, j("correct"))["pass"] is False
    assert S.combine(full, None) == {"rule_a": True, "rule_b": None, "pass": True, "judged": False}


def test_parse_judge():
    out = S.parse_judge('```json\n{"verdict": "Correct", "hallucination": "false", "reason": "đủ ý"}\n```')
    assert out == {"verdict": "correct", "hallucination": False, "reason": "đủ ý"}
    with pytest.raises(ValueError):
        S.parse_judge('{"verdict": "maybe"}')


def test_summarize():
    rs = [{"id": "a", "type": "lookup", "pass": True, "total_s": 10, "ttft_s": 5, "n_search": 1, "include_ratio": 1},
          {"id": "b", "type": "lookup", "pass": False, "total_s": 20, "ttft_s": 7, "n_search": 3, "include_ratio": 0.5},
          {"id": "c", "type": "table", "pass": False, "error": "x", "total_s": 1}]
    s = S.summarize(rs, 31.0)
    assert s["accuracy"] == 33.3 and s["n_error"] == 1
    assert s["accuracy_by_type"]["lookup"]["accuracy"] == 50.0
    assert s["total_s_p50"] == 15.0 and s["total_s_max"] == 20 and s["avg_n_search"] == 2


def test_meter_does_not_match_millimeter():
    from tests.eval.scoring import contains, normalize

    assert not contains(normalize("cách nhau 7mm"), "7m")
    assert contains(normalize("dán nhãn cách 7m tại chỗ xuyên tường"), "7 m")
    assert contains(normalize("duy trì 12 giờ thường trực"), "12 giờ")
