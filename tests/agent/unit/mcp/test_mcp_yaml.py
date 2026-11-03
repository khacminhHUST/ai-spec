"""Unit – server MCP trong config/mcp.yaml (stdio cục bộ, không gọi mạng): nạp tool, phân loại read/write, bộ tool,
mỗi server là 1 nguồn trong danh mục nguồn."""
import sys
from pathlib import Path

import pytest

from app.services.agent.config import agent_cfg
from app.services.agent.mcp.catalog import SourceCatalog, SourceUnavailable
from app.services.agent.mcp.yaml_servers import load_mcp_tools, server_connections, tool_access
from app.services.agent.sandbox import build_manager
from app.services.agent.skills import load_skills
from app.services.agent.tools.registry import access_of, build_toolset
from tests.query.fakes import make_deps

pytestmark = pytest.mark.unit

SERVER = str(Path(__file__).parents[2] / "fixtures" / "mcp_echo_server.py")


def _cfg(**server):
    return {"tool_name_prefix": True,
            "servers": {"echo": {"transport": "stdio", "command": sys.executable, "args": [SERVER], **server}}}


def test_tool_access_precedence():
    cfg = {"servers": {"a": {"access": "read", "tools": {"push": {"access": "write"}}}, "b": {}, "c": {"access": "x"}}}
    assert tool_access(cfg, "a", "get") == "read"
    assert tool_access(cfg, "a", "push") == "write"
    assert tool_access(cfg, "b", "get") == "write"            # không khai báo → write
    assert tool_access(cfg, "c", "get") == "write"            # giá trị lạ → write
    assert tool_access(cfg, "zzz", "get") == "write"


def test_server_connections_env_and_strip(monkeypatch):
    monkeypatch.setenv("T_TOKEN", "s3cret")
    conns = server_connections({"servers": {"x": {"transport": "streamable_http", "url": "https://h/mcp",
                                                  "headers": {"Authorization": "Bearer ${T_TOKEN}"},
                                                  "access": "read", "tools": {"a": {"access": "write"}},
                                                  "description": "Hệ thống X"}}})
    assert conns == {"x": {"transport": "streamable_http", "url": "https://h/mcp",
                           "headers": {"Authorization": "Bearer s3cret"}}}


def test_load_stdio_server_labels_and_sync_call():
    tools = {t.name: t for t in load_mcp_tools(_cfg(access="read", tools={"create_issue": {"access": "write"}}))}
    assert set(tools) == {"echo_lookup_issue", "echo_create_issue"}
    assert tools["echo_lookup_issue"].metadata["access"] == "read"
    assert tools["echo_create_issue"].metadata["access"] == "write"
    assert "issue A1: open" in str(tools["echo_lookup_issue"].invoke({"issue_id": "A1"}))   # gọi sync được


def test_bad_server_is_skipped():
    assert load_mcp_tools({"servers": {"bad": {"transport": "stdio", "command": sys.executable,
                                                "args": ["-c", "import sys; sys.exit(1)"]}}}) == []
    assert load_mcp_tools({"servers": {}}) == []


def test_registry_labels(mock_settings, tmp_path):
    mcp = load_mcp_tools(_cfg())                              # server không khai báo access → mọi tool write
    ts = build_toolset(make_deps(mock_settings), build_manager({"backend": "mock"}), agent_cfg(),
                       load_skills(tmp_path), mcp)
    assert ts.names[:6] == ["search_project_docs", "run_code", "read_file", "write_file", "list_files", "load_skill"]
    assert ts.write_tools == {"echo_lookup_issue", "echo_create_issue"}
    assert access_of(ts.get("search_project_docs")) == "read" and access_of(ts.get("run_code")) == "sandbox"


def test_yaml_server_is_a_source():
    cfg = _cfg(access="read", description="Quản lý issue công trường")
    catalog = SourceCatalog(load_mcp_tools(cfg), cfg)
    assert catalog.entries("u1") == [{"name": "echo", "label": "echo", "description": "Quản lý issue công trường",
                                      "status": "ready"}]
    assert {t.name for t in catalog.tools("u1", "echo")} == {"echo_lookup_issue", "echo_create_issue"}
    with pytest.raises(SourceUnavailable):
        catalog.tools("u1", "khac")
