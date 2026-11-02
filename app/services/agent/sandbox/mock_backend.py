"""MockSandbox: file trong bộ nhớ, exec theo `exec_handler` (unit test, không cần Docker)."""
from __future__ import annotations

import time
from typing import Callable

from app.services.agent.sandbox.base import (
    RUN_DIR, WORKSPACE, ExecResult, FileInfo, SandboxBackend, SandboxError, workspace_path,
)

ExecHandler = Callable[["MockSandbox", list[str]], ExecResult]


class MockSandbox(SandboxBackend):
    def __init__(self, exec_handler: ExecHandler | None = None):
        self.files: dict[str, tuple[bytes, float]] = {}
        self.exec_handler = exec_handler
        self.created = False
        self.destroyed = False
        self.exec_calls: list[list[str]] = []

    def create(self) -> None:
        self.created = True

    def exec(self, cmd: list[str], timeout_s: int) -> ExecResult:
        self.exec_calls.append(cmd)
        if self.exec_handler:
            return self.exec_handler(self, cmd)
        return ExecResult(0, "", "")

    def put_file(self, path: str, data: bytes) -> str:
        p = workspace_path(path)
        self.files[p] = (bytes(data), time.time())
        return p

    def get_file(self, path: str, max_bytes: int | None = None) -> bytes:
        p = workspace_path(path)
        if p not in self.files:
            raise SandboxError(f"Không có file: {p}")
        data = self.files[p][0]
        if max_bytes is not None and len(data) > max_bytes:
            raise SandboxError(f"File quá lớn ({len(data)} bytes): {p}")
        return data

    def list(self, dir: str = WORKSPACE) -> list[FileInfo]:
        d = workspace_path(dir).rstrip("/") + "/"
        return sorted((FileInfo(p, len(b), t) for p, (b, t) in self.files.items()
                       if p.startswith(d) and not p.startswith(RUN_DIR + "/")), key=lambda f: f.path)

    def destroy(self) -> None:
        self.files.clear()
        self.destroyed = True
