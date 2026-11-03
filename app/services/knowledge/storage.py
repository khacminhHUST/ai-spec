"""FileStore: lưu file gốc upload. Bản local: <root>/<project_id>/<file_hash>.pdf (sau này thay bằng object storage)."""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from app.services.knowledge.config import uploads_dir

_KEY_RE = re.compile(r"^[A-Za-z0-9.\-]+/[0-9a-f]{64}\.pdf$")


class FileStore(ABC):
    @abstractmethod
    def put(self, project_id: str, file_hash: str, src: Path) -> str:
        """Lưu file (đã tính hash) → storage_key. Đã có → giữ nguyên."""

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """Đường dẫn đọc được bằng parser (object storage: tải về cache cục bộ)."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalFileStore(FileStore):
    def __init__(self, root: Path | None = None):
        self.root = Path(root or uploads_dir())

    @staticmethod
    def make_key(project_id: str, file_hash: str) -> str:
        return f"{project_id}/{file_hash}.pdf"

    def local_path(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise ValueError(f"storage_key không hợp lệ: {key!r}")
        return self.root / key

    def exists(self, key: str) -> bool:
        return self.local_path(key).is_file()

    def put(self, project_id: str, file_hash: str, src: Path) -> str:
        key = self.make_key(project_id, file_hash)
        dst = self.local_path(key)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".part")
            shutil.copyfile(src, tmp)
            tmp.replace(dst)
        return key


def spool_upload(stream: BinaryIO, max_bytes: int, chunk: int = 1 << 20) -> tuple[Path, str, int]:
    """Ghi stream upload ra file tạm, tính sha256, chặn quá max_bytes → (path, file_hash, size).

    Vượt giới hạn → ValueError (file tạm đã xóa). Người gọi xóa file tạm sau khi dùng.
    """
    h, size = hashlib.sha256(), 0
    fd, name = tempfile.mkstemp(suffix=".pdf", prefix="mep_upload_")
    path = Path(name)
    try:
        with open(fd, "wb") as f:
            while data := stream.read(chunk):
                size += len(data)
                if size > max_bytes:
                    raise ValueError(f"file vượt giới hạn {max_bytes // (1024 * 1024)} MB")
                h.update(data)
                f.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, h.hexdigest(), size


def is_pdf(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(1024).lstrip().startswith(b"%PDF-")
