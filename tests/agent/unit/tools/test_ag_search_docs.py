"""Unit – search_project_docs (bọc N1–N6, không N7): quyền theo user_id của state, kết quả lớn → file, đánh số [n]."""
import json

import pytest

from app.services.agent.config import agent_cfg
from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.agent.state import AgentContext
from app.services.agent.tools.search_docs import SearchDocs, apply_hints, make_search_tool
from tests.agent.fakes import call_cmd
from tests.query.fakes import S5, S6, InMemoryRepo, make_deps, router_json, scripted_llm

pytestmark = pytest.mark.unit

BEND = router_json(q="Bán kính uốn cong tối thiểu của cáp tiết diện lớn hơn 25mm2",
                   queries_vi=["bán kính uốn cong tối thiểu cáp tiết diện lớn hơn 25mm2"],
                   queries_en=["minimum bend radius cable larger 25mm2"], exact_keywords=["25mm2"])
NOTHING = router_json(q="Giá trị hợp đồng gói thầu cơ điện", queries_vi=["zzqx wvyk"], queries_en=["zzqx wvyk"])


class SpyRepo(InMemoryRepo):
    def __init__(self, data, **kw):
        super().__init__(data, **kw)
        self.user_ids = []

    def user_projects(self, user_id):
        self.user_ids.append(user_id)
        return super().user_projects(user_id)


def _setup(mock_settings, router=BEND, max_tokens=None, users=None):
    from tests.fixtures.query_seed.seed import load_seed

    data = load_seed()
    llm = scripted_llm(router)
    repo = SpyRepo(data, users=users)
    deps = make_deps(mock_settings, data=data, llm=llm, repo=repo)
    boxes = {}

    def factory(cid):
        boxes[cid] = MockSandbox()
        return boxes[cid]

    cfg = {**agent_cfg()["agent"], **({"tool_result_max_tokens": max_tokens} if max_tokens else {})}
    tool = make_search_tool(SearchDocs(deps), SandboxManager(factory, 1800), cfg)
    return tool, deps, llm, repo, boxes


def test_ok_numbering_citations_and_no_generator(mock_settings):
    tool, deps, llm, _, _ = _setup(mock_settings, max_tokens=10**6)
    out, upd = call_cmd(tool, question="Bán kính uốn cáp > 25mm2?")
    assert out["ok"] and out["status"] in ("OK", "PARTIAL") and "full_result_file" not in out
    assert out["blocks"][0]["n"] == 1 and "uốn cong" in out["blocks"][0]["section"]
    assert any("6" in (b.get("vi") or "") for b in out["blocks"])
    assert [c["n"] for c in upd["sources"]] == [b["n"] for b in out["blocks"]]
    assert upd["sources"][0]["section_key"] == out["citations"][0]["section_key"]
    assert upd["active_projects"] == [S5]
    systems = [str(c[0].content) for c in llm.calls]
    assert not any(s.startswith("Bạn là trợ lý tra cứu") for s in systems)      # N7 không chạy
    # lần gọi thứ 2 trong cùng lượt: n nối tiếp nguồn đã có
    out2, upd2 = call_cmd(tool, state={"sources": upd["sources"], "active_projects": [S5]}, question="Lại?")
    assert out2["blocks"][0]["n"] == len(upd["sources"]) + 1


def test_user_id_from_state_not_llm_args(mock_settings):
    tool, _, _, repo, _ = _setup(mock_settings, max_tokens=10**6)
    out, _ = call_cmd(tool, ctx=AgentContext("test_engineer", "c1"), question="Bán kính uốn?", user_id="multi")
    assert out["ok"]
    assert set(repo.user_ids) == {"test_engineer"}                              # tham số user_id của LLM bị bỏ qua
    assert "user_id" not in tool.args


def test_project_without_permission_is_excluded(mock_settings):
    tool, deps, _, _, _ = _setup(mock_settings, max_tokens=10**6)
    out, upd = call_cmd(tool, question="Bán kính uốn cáp?", project_mentions=["S6"])    # user chỉ có quyền S5
    assert out["status"] == "ASK_CLARIFY" and "chưa có quyền" in out["message"]
    assert all(o["project_id"] != S6 for o in out["clarify_options"])
    assert upd["sources"] == [] and S6 not in upd["active_projects"]
    assert all(c["project_id"] != S6 for c in deps.retriever.calls)


def test_large_result_goes_to_sandbox_file(mock_settings):
    tool, _, _, _, boxes = _setup(mock_settings, max_tokens=50)
    out, upd = call_cmd(tool, question="Bán kính uốn cáp > 25mm2?")
    path = out["full_result_file"]
    assert path.startswith("input/search_") and "read_file" in out["note"]
    full = json.loads(boxes["conv-1"].get_file(path))
    assert full["blocks"][0]["vi"] and len(full["blocks"]) == len(out["blocks"])
    assert len(json.dumps(out, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False))
    assert all(len(b.get("vi", "")) <= 450 for b in out["blocks"])
    assert len(upd["sources"]) == len(full["citations"])


def test_not_found_has_no_blocks(mock_settings):
    tool, _, _, _, _ = _setup(mock_settings, router=NOTHING, max_tokens=10**6)
    out, upd = call_cmd(tool, question="Giá trị hợp đồng gói thầu cơ điện?")
    assert out["status"] == "NOT_FOUND" and out["blocks"] == [] and "không được tự điền" in out["message"]
    assert upd["sources"] == []


def test_apply_hints():
    r = json.loads(router_json(q="x", streams=["CONTRACT"]))
    h = apply_hints(r, ["S5"], ["hvac", "BOGUS"], ["OPERATOR_STANDARD", "X"])
    assert h["project_action"] == "set" and h["project_mentions"] == ["S5"]
    assert h["disciplines"] == ["HVAC"] and h["streams"] == ["OPERATOR_STANDARD"]
    assert apply_hints(r, None, None, None)["streams"] == ["CONTRACT"]
