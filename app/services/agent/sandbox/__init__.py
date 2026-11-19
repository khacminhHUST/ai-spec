"""Sandbox chạy code: interface SandboxBackend + quản lý vòng đời theo conversation."""
from app.services.agent.sandbox.base import (
    INPUT_DIR, OUTPUT_DIR, WORKSPACE, ExecResult, FileInfo, SandboxBackend, SandboxBusy, SandboxError,
    workspace_path,
)
from app.services.agent.sandbox.manager import SandboxManager, build_manager

__all__ = ["INPUT_DIR", "OUTPUT_DIR", "WORKSPACE", "ExecResult", "FileInfo", "SandboxBackend", "SandboxBusy", "SandboxError",
           "SandboxManager", "build_manager", "workspace_path"]
