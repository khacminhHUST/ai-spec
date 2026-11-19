import dataclasses

import pytest

from app.providers.embedding import MockEmbedder, get_embedder
from app.providers.llm import MockChatModel, get_chat_model
from app.providers.reranker import MockReranker, get_reranker
from app.utils.sparse import doc_sparse, query_sparse, token_id, tokenize

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text,token", [
    ("theo TCVN 5687:2024 về thông gió", "tcvn5687:2024"),
    ("tiết diện 1.5mm² trở lên", "1.5mm2"),
    ("tiết diện 1.5 mm2", "1.5mm2"),
    ("xem mục 5.7.1", "5.7.1"),
    ("ống DN65", "dn65"),
    ("cáp LSZH chống cháy", "lszh"),
    ("2.5 L/s.người", "2.5l/s.người"),
])
def test_tokenize_keeps_codes(text, token):
    assert token in tokenize(text)


def test_tokenize_vietnamese_words():
    assert tokenize("Bán kính uốn cong") == ["bán", "kính", "uốn", "cong"]


def test_sparse_vectors():
    d = doc_sparse("cáp 1.5mm² cáp chiếu sáng")
    assert len(d["indices"]) == len(set(d["indices"])) == len(d["values"])
    q = query_sparse("tiết diện cáp", ["1.5mm2"], boost=2.0)
    w = dict(zip(q["indices"], q["values"]))
    assert w[token_id("1.5mm2")] == 2.0 and w[token_id("cáp")] == 1.0


def test_get_providers_mock(mock_settings):
    for lang in ("vi", "en"):
        e = get_embedder(lang, mock_settings)
        assert isinstance(e, MockEmbedder)
        assert e.dim == int(mock_settings.embedding_cfg(lang)["dimensions"])
    assert isinstance(get_reranker(mock_settings), MockReranker)
    assert isinstance(get_chat_model("fast", mock_settings), MockChatModel)
    assert isinstance(get_chat_model("chunking", mock_settings), MockChatModel)


def test_mock_when_key_missing(settings):
    s = dataclasses.replace(settings, mock=frozenset(), deepinfra_api_key=None, google_api_key=None)
    assert s.is_mock("deepinfra") and s.is_mock("google_genai") and not s.is_mock("local")
    assert isinstance(get_embedder("vi", s), MockEmbedder) and isinstance(get_chat_model("main", s), MockChatModel)


def test_mock_embedder_similarity(mock_settings):
    e = get_embedder("vi", mock_settings)
    q = e.embed_query("bán kính uốn cáp")
    d1, d2 = e.embed_documents(["bán kính uốn cong của cáp", "thời gian bảo hành 24 tháng"])
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731
    assert dot(q, d1) > dot(q, d2)


def test_mock_reranker():
    s = MockReranker().score("bán kính uốn cáp", ["bán kính uốn cáp 6 lần", "bảo hành", ""])
    assert s[0] == 1.0 and s[1] == 0.0 and all(0 <= x <= 1 for x in s)


def test_mock_chat_model():
    m = MockChatModel(responses=["a", "b"])
    assert m.invoke("x").content == "a"
    assert m.invoke("x").content == "b"
    assert m.invoke("x").content == "b"
    assert len(m.calls) == 3
    h = MockChatModel(handler=lambda msgs: msgs[-1].content.upper())
    assert "".join(c.content for c in h.stream("hello world")) == "HELLO WORLD"


def test_gemini_reranker_cham_diem_va_bo_sot():
    """LLM trả điểm 0–10 theo id → /10; id thiếu → missing_score (giữ lại); lô lớn chia nhỏ."""
    import json
    import re

    from app.providers.reranker import GeminiReranker

    def handler(msgs):
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", msgs[-1].content, re.M)]
        return json.dumps({"scores": [{"id": i, "score": 9 if "gió tươi" in msgs[-1].content.split(f"[{i}]")[1][:40]
                                       else 1} for i in ids[:-1]]})   # cố tình bỏ sót đoạn cuối mỗi lô

    llm = MockChatModel(handler=handler, calls=[])
    r = GeminiReranker({"model": "x", "batch_size": 2, "gemini_min_score": 0.3}, settings=None, llm=llm)
    sc = r.score("gió tươi gym?", ["gió tươi 10 l/s", "chiếu sáng", "cáp điện"])
    assert sc == [0.9, 0.3, 0.3] and len(llm.calls) == 3   # 2 lô; lô 2 không có điểm → thử lại 1 lần; đoạn sót → 0.3


def test_gemini_reranker_loi_giu_nguyen():
    from app.providers.reranker import GeminiReranker

    r = GeminiReranker({"model": "x", "gemini_min_score": 0.3}, settings=None, llm=MockChatModel(responses=["không phải JSON"],
                                                                                           calls=[]))
    assert r.score("q", ["a", "b"]) == [0.3, 0.3]
    assert GeminiReranker.parse('{"scores": [{"id": 0, "score": 12}, {"id": 9, "score": 5}, {"id": "x"}]}', 2) == {0: 1.0}
