import pytest

from shared.config import get_settings
from shared.migrate import alias_rows
from shared.tokens import count_tokens

pytestmark = pytest.mark.unit


def test_configs_loaded(settings):
    assert settings.embedding_cfg("vi")["collection"] == "child_chunks_vi"
    assert settings.pipeline["rerank"]["min_score"] == 0.3
    assert settings.project_list()[0]["project_id"] == "26DA06"


def test_namespace(settings):
    s = settings.with_ns("query")
    assert s.pg_schema == "query" and s.collection("en") == "query__child_chunks_en"
    assert settings.with_ns("").collection("vi") == "child_chunks_vi"
    with pytest.raises(ValueError):
        settings.with_ns("bad-ns; drop")


def test_data_root_has_samples(settings):
    root = settings.data_root / settings.project_list()[0]["buildings"][0]["source_root"]
    assert root.is_dir(), f"không thấy bộ tài liệu mẫu tại {root} (MEP_DATA_ROOT)"


def test_alias_rows(settings):
    rows = {(r[0], r[3]) for r in alias_rows(settings)}
    assert ("baidatdo", "") in rows and ("bdd", "") in rows and ("s5", "S5") in rows


def test_count_tokens():
    assert count_tokens("") == 0
    assert 0 < count_tokens("Bán kính uốn cong tối thiểu của cáp", "vi") < 40
    assert get_settings() is get_settings()
