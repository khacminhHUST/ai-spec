"""MCP server stdio giả lập cho test: 1 tool đọc (lookup_issue), 1 tool ghi (create_issue).

MCP_ECHO_LOG: đường dẫn file ghi lại lời gọi create_issue (để E2E kiểm tra tool có thực sự chạy không).
"""
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def lookup_issue(issue_id: str) -> str:
    """Tra cứu issue theo mã."""
    return f"issue {issue_id}: open"


@mcp.tool()
def create_issue(title: str) -> str:
    """Tạo issue mới trên hệ thống quản lý issue ngoài."""
    log = os.environ.get("MCP_ECHO_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(title + "\n")
    return f"created: {title}"


if __name__ == "__main__":
    mcp.run("stdio")
