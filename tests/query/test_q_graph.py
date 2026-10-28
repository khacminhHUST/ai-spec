"""Unit – Graph chạy đủ 4 đường OK / PARTIAL / NOT_FOUND / ASK_CLARIFY (mock LLM, embedding, reranker)."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.services.query.graph import build_graph, run_turn, stream_turn
from tests.query.fakes import S5, make_deps, router_json, scripted_llm

pytestmark = pytest.mark.unit

BEND = router_json(q="Bán kính uốn cong tối thiểu của cáp tiết diện lớn hơn 25mm2",
                   queries_vi=["bán kính uốn cong tối thiểu cáp tiết diện lớn hơn 25mm2"],
                   queries_en=["minimum bend radius cable larger 25mm2"], exact_keywords=["25mm2"])


def _graph(deps):
    return build_graph(deps, InMemorySaver())


def test_path_ok(mock_settings):
    llm = scripted_llm(BEND, answer="Cáp tiết diện lớn hơn 25mm2: 6 lần đường kính ngoài của cáp [1].")
    deps = make_deps(mock_settings, llm=llm)
    out = run_turn(_graph(deps), "test_engineer", "Bán kính uốn cong cáp > 25mm2?")
    assert out["status"] == "OK" and out["hop_count"] == 0
    assert out["active_projects"] == [S5]
    assert out["context"][0]["section_key"] == "table_5_7_1"
    assert out["citations"][0]["section_key"] == "table_5_7_1" and out["citations"][0]["page"] == 21
    assert len(llm.calls) == 2           # router + generator (top1 ≥ 0.5 → không gọi grader)
    assert {c["lang"] for c in deps.retriever.calls} == {"vi", "en"}


def test_path_partial_max_one_hop(mock_settings):
    class Low:
        def score(self, q, docs):
            return [0.35] * len(docs)

    llm = scripted_llm(BEND, grader='{"verdict": "PARTIAL", "missing_query": "bán kính uốn cáp mềm"}',
                       answer="Một phần [1]. Thiếu thông tin cáp mềm.")
    deps = make_deps(mock_settings, llm=llm)
    deps.reranker = Low()
    out = run_turn(_graph(deps), "test_engineer", "Bán kính uốn?")
    assert out["status"] == "PARTIAL" and out["hop_count"] == 1
    tf_calls = [c for c in deps.retriever.calls if c["queries"] == ["bán kính uốn cáp mềm"]]
    assert len(tf_calls) == 2               # 1 lần search lại / nhánh (vi, en) – chỉ 1 hop
    grader_calls = [c for c in llm.calls if str(c[0].content).startswith("Bạn kiểm tra")]
    assert len(grader_calls) == 2           # trước và sau Targeted Fetch, không lặp thêm


def test_stream_without_docs_falls_back(mock_settings):
    """Router chọn stream CONTRACT (chưa có tài liệu) → tìm trên mọi stream có tài liệu, không NOT_FOUND giả."""
    llm = scripted_llm(router_json(q="Thời gian bảo hành hệ thống điện?", intent_type="CONTRACT_TERM",
                                   streams=["CONTRACT"], queries_vi=["thời gian bảo hành hệ thống điện"]),
                       answer="Bảo hành 24 tháng [1].")
    deps = make_deps(mock_settings, llm=llm)
    run_turn(_graph(deps), "test_engineer", "Thời gian bảo hành hệ thống điện?")
    assert deps.retriever.calls and all(c["stream"] != "CONTRACT" for c in deps.retriever.calls)


def test_path_not_found_below_threshold(mock_settings):
    llm = scripted_llm(router_json(q="xyz", queries_vi=["cáp"]), answer="Không có.")
    deps = make_deps(mock_settings, llm=llm)
    out = run_turn(_graph(deps), "test_engineer", "xyz")   # MockReranker: 'xyz' không trùng token → < 0.3
    assert out["status"] == "NOT_FOUND" and out["reranked"] == []


def test_path_ask_clarify(mock_settings):
    llm = scripted_llm(router_json(q="Màu dây pha?"))
    deps = make_deps(mock_settings, llm=llm)
    out = run_turn(_graph(deps), "multi", "Màu dây pha?")
    assert out["status"] == "ASK_CLARIFY" and "chọn" in out["answer"]
    assert len(out["clarify_options"]) == 4 and len(llm.calls) == 1   # chỉ router, không gọi generator
    assert out["messages"][-1]["status"] == "ASK_CLARIFY"


def test_multi_turn_keeps_project_and_resets_transient(mock_settings):
    gym = router_json(q="Gió tươi phòng gym S5", project_action="set", project_mentions=["S5"],
                      queries_vi=["gió tươi health club gym"])
    lounge = router_json(q="Gió tươi lounge S5", queries_vi=["gió tươi lounges"])
    llm = scripted_llm([gym, lounge], answer="Theo Bảng 4.3 [1].")
    deps = make_deps(mock_settings, llm=llm)
    g = _graph(deps)
    t1 = run_turn(g, "multi", "Gió tươi phòng gym S5?")
    t2 = run_turn(g, "multi", "Còn lounge thì sao?", t1["conversation_id"])
    assert t1["active_projects"] == t2["active_projects"] == [S5]
    assert len(t2["messages"]) == 4 and t2["messages"][0]["content"] == "Gió tươi phòng gym S5?"
    router_prompt_t2 = [c for c in llm.calls if str(c[0].content).startswith("Bạn là bộ định tuyến")][1][-1].content
    assert "Gió tươi phòng gym S5?" in router_prompt_t2


def test_candidates_reset_each_turn(mock_settings):
    deps = make_deps(mock_settings, llm=scripted_llm(BEND))
    g = _graph(deps)
    t1 = run_turn(g, "test_engineer", "q1")
    t2 = run_turn(g, "test_engineer", "q2", t1["conversation_id"])
    assert len(t1["candidates"]) == len(t2["candidates"]) > 0


def test_stream_events(mock_settings):
    deps = make_deps(mock_settings, llm=scripted_llm(BEND, answer="6 lần đường kính ngoài [1]."))
    events = list(stream_turn(_graph(deps), "test_engineer", "Bán kính?"))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "status" and kinds[-2:] == ["citations", "done"]
    assert "".join(e["data"] for e in events if e["event"] == "token") == "6 lần đường kính ngoài [1]."
    assert events[-1]["data"]["status"] == "OK"


def test_partial_irrelevant_without_citation_becomes_not_found(mock_settings):
    class Low:
        def score(self, q, docs):
            return [0.35] * len(docs)

    llm = scripted_llm(BEND, grader='{"verdict": "NONE", "missing_query": ""}',
                       answer="Không tìm thấy thông tin này trong tài liệu dự án.")
    deps = make_deps(mock_settings, llm=llm)
    deps.reranker = Low()
    out = run_turn(_graph(deps), "test_engineer", "Giá trị hợp đồng?")
    assert out["status"] == "NOT_FOUND" and out["citations"] == [] and out["hop_count"] == 1


def test_partial_irrelevant_but_cited_stays_partial(mock_settings):
    class Low:
        def score(self, q, docs):
            return [0.35] * len(docs)

    llm = scripted_llm(BEND, grader='{"verdict": "NONE"}', answer="6 lần đường kính ngoài [1].")
    deps = make_deps(mock_settings, llm=llm)
    deps.reranker = Low()
    out = run_turn(_graph(deps), "test_engineer", "Bán kính uốn?")
    assert out["status"] == "PARTIAL" and out["citations"]
