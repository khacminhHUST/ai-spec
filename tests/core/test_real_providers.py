"""Gọi API thật – provider bị mock (MEP_MOCK / thiếu key) thì skip."""
import pytest

from app.providers.embedding import MockEmbedder, get_embedder
from app.providers.llm import MockChatModel, get_chat_model
from app.providers.reranker import MockReranker, get_reranker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("lang", ["vi", "en"])
def test_embedding_api(settings, lang):
    e = get_embedder(lang, settings)
    if isinstance(e, MockEmbedder):
        pytest.skip(f"embedding {lang} đang mock")
    docs = e.embed_documents(["bend radius of cable 6 times outside diameter", "warranty 24 months", "fresh air"])
    assert len(docs) == 3 and all(len(v) == e.dim for v in docs)
    q = e.embed_query("minimum bend radius of cable")
    dot = [sum(a * b for a, b in zip(q, d)) for d in docs]
    assert dot[0] == max(dot)


def test_reranker_api(settings):
    r = get_reranker(settings)
    if isinstance(r, MockReranker):
        pytest.skip("reranker đang mock")
    s = r.score("bán kính uốn cáp", ["Bán kính uốn cong tối thiểu 6 lần đường kính ngoài", "Bảo hành 24 tháng"])
    assert s[0] > s[1]


@pytest.mark.parametrize("role", ["fast", "main", "chunking"])
def test_llm_api(settings, role):
    m = get_chat_model(role, settings)
    if isinstance(m, MockChatModel):
        pytest.skip("LLM đang mock")
    assert "OK" in m.invoke("Reply with exactly: OK").text
