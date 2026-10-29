"""MCP tool ngoài (config/mcp.yaml)."""
from app.services.agent.mcp.client import load_mcp_tools, tool_access

__all__ = ["load_mcp_tools", "tool_access"]
