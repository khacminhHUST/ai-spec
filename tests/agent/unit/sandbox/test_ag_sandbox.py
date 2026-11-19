"""Unit – SandboxBackend (Mock), kiểm tra đường dẫn, vòng đời SandboxManager."""
import pytest

from app.services.agent.sandbox import SandboxError, SandboxManager, build_manager, workspace_path
from app.services.agent.sandbox.mock_backend import MockSandbox

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("path,expected", [
    ("output/a.xlsx", "/workspace/output/a.xlsx"),
    ("/workspace/input/x.json", "/workspace/input/x.json"),
    ("output/../input/b.txt", "/workspace/input/b.txt"),
    ("output\c.docx", "/workspace/output/c.docx"),
])
def test_workspace_path_ok(path, expected):
    assert workspace_path(path) == expected


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "/workspace/../etc/x", "/workspacex/a", "/tmp/a"])
def test_workspace_path_rejects(path):
    with pytest.raises(SandboxError):
        workspace_path(path)


def test_mock_files_roundtrip():
    sbx = MockSandbox()
    sbx.put_file("output/a.txt", b"xin chao")
    sbx.put_file("/workspace/.run/run_1.py", b"print(1)")
    assert sbx.get_file("/workspace/output/a.txt") == b"xin chao"
    assert [f.path for f in sbx.list()] == ["/workspace/output/a.txt"]   # .run bị ẩn
    with pytest.raises(SandboxError):
        sbx.get_file("output/missing.txt")
    with pytest.raises(SandboxError):
        sbx.get_file("output/a.txt", max_bytes=3)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_manager_one_sandbox_per_conversation_and_idle_ttl():
    clock, made = Clock(), []

    def factory(cid):
        made.append(MockSandbox())
        return made[-1]

    m = SandboxManager(factory, idle_ttl_s=100, clock=clock)
    a1, a2, b = m.get("a"), m.get("a"), m.get("b")
    assert a1 is a2 and a1 is not b and a1.created
    assert m.peek("c") is None and len(made) == 2      # peek không tạo mới
    clock.t = 50
    m.get("a")                                          # a được dùng lại → còn sống
    clock.t = 120
    assert m.reap_idle() == ["b"] and b.destroyed and not a1.destroyed
    assert m.destroy("a") and a1.destroyed and not m.destroy("a")


def test_manager_tran_so_sandbox():
    from app.services.agent.sandbox import SandboxBusy

    clock = Clock()
    m = SandboxManager(lambda cid: MockSandbox(), idle_ttl_s=100, clock=clock, max_sandboxes=2)
    m.get("a"), m.get("b")
    assert m.get("a")                                   # hội thoại đã có sandbox → vẫn dùng được
    with pytest.raises(SandboxBusy):
        m.get("c")
    clock.t = 150                                       # a, b idle quá TTL → được dọn → c tạo được
    assert m.get("c") and m.peek("a") is None


def test_build_manager_mock():
    m = build_manager({"backend": "mock", "idle_ttl_s": 5, "max_sandboxes": 3})
    assert isinstance(m.get("x"), MockSandbox) and m.idle_ttl_s == 5 and m.max_sandboxes == 3
