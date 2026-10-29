"""MCP server stdio giả lập cho test: 1 tool đọc (lookup_issue), 1 tool ghi (create_issue)."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def lookup_issue(issue_id: str) -> str:
    """Tra cứu issue theo mã."""
    return f"issue {issue_id}: open"


@mcp.tool()
def create_issue(title: str) -> str:
    """Tạo issue mới trên hệ thống ngoài."""
    return f"created: {title}"


if __name__ == "__main__":
    mcp.run("stdio")
