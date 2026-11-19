"""Interface SandboxBackend: 1 instance = 1 sandbox (container) của 1 conversation.

Mọi đường dẫn là đường dẫn trong sandbox, bắt buộc nằm dưới /workspace (kiểm tra bằng `workspace_path`).
"""
from __future__ import annotations

import posixpath
from abc import ABC, abstractmethod
from dataclasses import dataclass

WORKSPACE = "/workspace"
INPUT_DIR = f"{WORKSPACE}/input"
OUTPUT_DIR = f"{WORKSPACE}/output"
RUN_DIR = f"{WORKSPACE}/.run"     # file code tạm của run_code (ẩn khỏi list_files)


class SandboxError(Exception):
    """Lỗi thao tác sandbox (đường dẫn sai, file không có, quá cỡ…) – tool chuyển thành thông báo cho agent."""


class SandboxBusy(SandboxError):
    """Đã đủ trần số sandbox (agent.yaml › sandbox.max_sandboxes) – không tạo thêm, người dùng thử lại sau."""


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class FileInfo:
    path: str        # đường dẫn tuyệt đối trong sandbox
    size: int
    mtime: float


def workspace_path(path: str, base: str = WORKSPACE) -> str:
    """Chuẩn hóa đường dẫn (tương đối tính từ `base`) và ép nằm trong /workspace."""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        raise SandboxError("Đường dẫn rỗng")
    full = posixpath.normpath(p if p.startswith("/") else posixpath.join(base, p))
    if full != WORKSPACE and not full.startswith(WORKSPACE + "/"):
        raise SandboxError(f"Chỉ được truy cập trong {WORKSPACE}: {path}")
    return full


class SandboxBackend(ABC):
    @abstractmethod
    def create(self) -> None:
        """Tạo sandbox (idempotent) với các thư mục input/output."""

    @abstractmethod
    def exec(self, cmd: list[str], timeout_s: int) -> ExecResult:
        """Chạy lệnh trong sandbox; quá `timeout_s` → dừng mọi tiến trình, timed_out=True."""

    @abstractmethod
    def put_file(self, path: str, data: bytes) -> str:
        """Ghi file (tạo thư mục cha). → đường dẫn tuyệt đối."""

    @abstractmethod
    def get_file(self, path: str, max_bytes: int | None = None) -> bytes:
        """Đọc file; không có → SandboxError; vượt `max_bytes` → SandboxError."""

    @abstractmethod
    def list(self, dir: str = WORKSPACE) -> list[FileInfo]:
        """Liệt kê đệ quy file trong `dir` (bỏ qua RUN_DIR)."""

    @abstractmethod
    def destroy(self) -> None:
        """Hủy sandbox và toàn bộ file (idempotent)."""
