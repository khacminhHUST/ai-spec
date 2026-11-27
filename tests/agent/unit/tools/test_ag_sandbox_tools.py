"""Unit – tool run_code / read_file / write_file / list_files trên MockSandbox."""
import pytest

from app.services.agent.config import agent_cfg
from app.services.agent.sandbox import ExecResult, SandboxManager
from app.services.agent.sandbox.mock_backend import MockSandbox
from app.services.agent.state import AgentContext
from app.services.agent.tools.sandbox_tools import make_sandbox_tools
from tests.agent.fakes import call

pytestmark = pytest.mark.unit


def _setup(handler=None, **cfg_over):
    cfg = {**agent_cfg()["sandbox"], **cfg_over}
    boxes = {}

    def factory(cid):
        boxes[cid] = MockSandbox(handler)
        return boxes[cid]

    tools = {t.name: t for t in make_sandbox_tools(SandboxManager(factory, 1800), cfg)}
    return tools, boxes, cfg


def test_run_code_reports_new_output_files_and_truncates():
    def handler(sbx, cmd):
        assert cmd[0] == "python" and cmd[1].startswith("/workspace/.run/")
        assert b"to_excel" in sbx.get_file(cmd[1])
        sbx.put_file("output/a.xlsx", b"PK..")
        return ExecResult(0, "x" * 10000, "")

    tools, boxes, cfg = _setup(handler, output_max_chars=100)
    out = call(tools["run_code"], code="df.to_excel('/workspace/output/a.xlsx')")
    assert out["ok"] and out["exit_code"] == 0
    assert out["new_files"] == [{"path": "output/a.xlsx", "size": 4}]
    assert len(out["stdout"]) < 200 and "cắt" in out["stdout"]
    assert list(boxes) == ["conv-1"]                  # conversation_id lấy từ context


def test_run_code_error_and_timeout_are_structured():
    results = iter([ExecResult(1, "", "Traceback…\nNameError: x"), ExecResult(137, "", "", timed_out=True)])
    tools, _, _ = _setup(lambda sbx, cmd: next(results))
    err = call(tools["run_code"], code="x")
    assert not err["ok"] and "NameError" in err["stderr"]
    to = call(tools["run_code"], code="while True: pass", timeout_s=10_000)
    assert not to["ok"] and to["timed_out"] and "Quá thời gian 120s" in to["error"]   # timeout bị chặn trần


def test_timeout_capped_by_config():
    seen = []

    class Box(MockSandbox):
        def exec(self, cmd, timeout_s):
            seen.append(timeout_s)
            return ExecResult(0, "", "")

    cfg = {**agent_cfg()["sandbox"]}
    tools = {t.name: t for t in make_sandbox_tools(SandboxManager(lambda cid: Box(), 1800), cfg)}
    call(tools["run_code"], code="pass")
    call(tools["run_code"], code="pass", timeout_s=5)
    call(tools["run_code"], code="pass", timeout_s=9999)
    assert seen == [cfg["exec_timeout_s"], 5, cfg["max_exec_timeout_s"]]


def test_file_tools_roundtrip_and_isolation():
    tools, boxes, _ = _setup(read_file_max_chars=10)
    assert call(tools["write_file"], path="output/note.md", content="Xin chào thế giới!")["ok"]
    r1 = call(tools["read_file"], path="output/note.md")
    assert r1["content"] == "Xin chào t" and r1["next_offset"] == 10
    r2 = call(tools["read_file"], path="output/note.md", offset=r1["next_offset"])
    assert r2["content"] == "hế giới!" and r2["next_offset"] is None
    assert call(tools["list_files"], dir="output")["files"] == [{"path": "output/note.md", "size": 23}]
    other = AgentContext("u2", "conv-2")
    assert call(tools["list_files"], ctx=other)["files"] == []          # conversation khác không thấy
    assert set(boxes) == {"conv-1", "conv-2"}


@pytest.mark.parametrize("tool,args", [
    ("read_file", {"path": "/etc/passwd"}),
    ("write_file", {"path": "../app/x.py", "content": "x"}),
    ("list_files", {"dir": "/"}),
    ("read_file", {"path": "output/missing.txt"}),
])
def test_file_tools_errors_are_structured(tool, args):
    tools, _, _ = _setup()
    out = call(tools[tool], **args)
    assert out["ok"] is False and out["error"]


def test_read_binary_file_suggests_run_code():
    tools, boxes, _ = _setup()
    call(tools["list_files"])
    boxes["conv-1"].put_file("input/a.xlsx", b"PK\x03\x04\x00\x00")
    out = call(tools["read_file"], path="input/a.xlsx")
    assert not out["ok"] and "run_code" in out["error"]
