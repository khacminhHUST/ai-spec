"""DockerSandbox: 1 container + 1 named volume (/workspace) cho mỗi conversation.

Ràng buộc: network none, rootfs read-only, chỉ ghi /workspace (volume) và /tmp (tmpfs), user không root,
cap_drop ALL, no-new-privileges, giới hạn CPU/RAM/pids, không truyền biến môi trường nào từ harness.
Timeout: `timeout -s KILL` trong container + chặn phía client; quá hạn → restart container để dọn mọi
tiến trình còn sót (fork bomb…) – file trong volume vẫn giữ.
"""
from __future__ import annotations

import io
import logging
import posixpath
import re
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from app.services.agent.sandbox.base import (
    RUN_DIR, WORKSPACE, ExecResult, FileInfo, SandboxBackend, SandboxError, workspace_path,
)

log = logging.getLogger(__name__)

LABEL = "mep.sandbox"
SANDBOX_UID = 1000          # user `sandbox` trong sandbox/image/Dockerfile
CLIENT_GRACE_S = 10         # chờ thêm phía client sau timeout trong container
_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def _docker():
    import docker

    return docker


class DockerSandbox(SandboxBackend):
    def __init__(self, sandbox_id: str, cfg: dict[str, Any], client: Any = None):
        self.sandbox_id = _ID_RE.sub("-", sandbox_id)[:60]
        self.cfg = cfg
        self._client = client
        self._container = None

    @property
    def name(self) -> str:
        return f"mep-sbx-{self.sandbox_id}"

    @property
    def client(self):
        if self._client is None:
            self._client = _docker().from_env()
        return self._client

    # ---- vòng đời ----
    def create(self) -> None:
        errors = _docker().errors
        if self._container is not None:
            return
        try:
            ct = self.client.containers.get(self.name)
            if ct.status != "running":
                ct.start()
            self._container = ct
            return
        except errors.NotFound:
            pass
        labels = {LABEL: "1", f"{LABEL}.id": self.sandbox_id}
        try:
            self.client.volumes.get(self.name)
        except errors.NotFound:
            self.client.volumes.create(self.name, labels=labels)
        c = self.cfg
        self._container = self.client.containers.run(
            c["image"], ["sleep", "infinity"], name=self.name, detach=True, labels=labels,
            runtime=c.get("runtime") or None, network_mode="none", read_only=True,
            user=f"{SANDBOX_UID}:{SANDBOX_UID}", cap_drop=["ALL"], security_opt=["no-new-privileges"],
            nano_cpus=int(float(c["cpus"]) * 1e9), mem_limit=c["memory"], memswap_limit=c["memory"],
            pids_limit=int(c["pids_limit"]), tmpfs={"/tmp": f"size={c['tmp_size']},mode=1777"},
            volumes={self.name: {"bind": WORKSPACE, "mode": "rw"}}, environment={},
        )

    def destroy(self) -> None:
        errors = _docker().errors
        for get, kw in ((self.client.containers.get, {"force": True}), (self.client.volumes.get, {"force": True})):
            try:
                get(self.name).remove(**kw)
            except errors.NotFound:
                pass
        self._container = None

    @classmethod
    def destroy_all(cls, client: Any = None) -> int:
        """Dọn mọi sandbox còn sót (VD sau khi app khởi động lại)."""
        client = client or _docker().from_env()
        n = 0
        for ct in client.containers.list(all=True, filters={"label": LABEL}):
            ct.remove(force=True)
            n += 1
        for vol in client.volumes.list(filters={"label": LABEL}):
            vol.remove(force=True)
        return n

    @property
    def container(self):
        if self._container is None:
            self.create()
        return self._container

    # ---- exec ----
    def exec(self, cmd: list[str], timeout_s: int) -> ExecResult:
        ct = self.container
        wrapped = ["timeout", "-s", "KILL", f"{int(timeout_s)}s", *cmd]
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(ct.exec_run, wrapped, demux=True, user=f"{SANDBOX_UID}:{SANDBOX_UID}", workdir=WORKSPACE)
        try:
            res = fut.result(timeout=timeout_s + CLIENT_GRACE_S)
        except FutureTimeout:
            self._restart()
            pool.shutdown(wait=False)
            return ExecResult(137, "", f"Quá thời gian {timeout_s}s", timed_out=True)
        pool.shutdown(wait=False)
        out, err = res.output or (None, None)
        result = ExecResult(res.exit_code if res.exit_code is not None else -1,
                            (out or b"").decode("utf-8", "replace"), (err or b"").decode("utf-8", "replace"))
        if result.exit_code in (124, 137):
            result.timed_out = True
            result.stderr += f"\nQuá thời gian {timeout_s}s hoặc bị dừng (exit {result.exit_code})"
            self._restart()
        return result

    def _restart(self) -> None:
        """Restart = giết mọi tiến trình (kể cả khi đã cạn pids); volume /workspace giữ nguyên."""
        try:
            self.container.restart(timeout=0)
        except Exception:  # noqa: BLE001
            log.exception("restart sandbox %s lỗi → tạo lại", self.name)
            self._container = None
            try:
                self.client.containers.get(self.name).remove(force=True)
            except Exception:  # noqa: BLE001
                pass
            self.create()

    # ---- file ----
    def put_file(self, path: str, data: bytes) -> str:
        p = workspace_path(path)
        rel = posixpath.relpath(p, WORKSPACE)
        if rel == ".":
            raise SandboxError("Đường dẫn phải là file")
        buf = io.BytesIO()
        now = time.time()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            parts = rel.split("/")
            for i in range(1, len(parts)):   # thư mục cha thuộc user sandbox
                d = tarfile.TarInfo("/".join(parts[:i]))
                d.type, d.mode, d.uid, d.gid, d.mtime = tarfile.DIRTYPE, 0o755, SANDBOX_UID, SANDBOX_UID, now
                tar.addfile(d)
            ti = tarfile.TarInfo(rel)
            ti.size, ti.mode, ti.uid, ti.gid, ti.mtime = len(data), 0o644, SANDBOX_UID, SANDBOX_UID, now
            tar.addfile(ti, io.BytesIO(data))
        if not self.container.put_archive(WORKSPACE, buf.getvalue()):
            raise SandboxError(f"Ghi file thất bại: {p}")
        return p

    def get_file(self, path: str, max_bytes: int | None = None) -> bytes:
        errors = _docker().errors
        p = workspace_path(path)
        try:
            stream, stat = self.container.get_archive(p)
        except errors.NotFound:
            raise SandboxError(f"Không có file: {p}") from None
        if stat.get("mode", 0) & (1 << 31):   # os.ModeDir của Go
            raise SandboxError(f"Là thư mục, không phải file: {p}")
        if max_bytes is not None and stat.get("size", 0) > max_bytes:
            raise SandboxError(f"File quá lớn ({stat['size']} bytes > {max_bytes}): {p}")
        with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
            member = next((m for m in tar.getmembers() if m.isfile()), None)
            if member is None:
                raise SandboxError(f"Không đọc được file: {p}")
            return tar.extractfile(member).read()

    def list(self, dir: str = WORKSPACE) -> list[FileInfo]:
        d = workspace_path(dir)
        res = self.exec(["find", d, "-path", RUN_DIR, "-prune", "-o", "-type", "f",
                         "-printf", r"%p\t%s\t%T@\n"], timeout_s=30)
        if res.exit_code != 0:
            if "No such file" in res.stderr:
                return []
            raise SandboxError(f"Liệt kê {d} lỗi: {res.stderr.strip()[:300]}")
        files = []
        for line in res.stdout.splitlines():
            parts = line.rsplit("\t", 2)
            if len(parts) == 3:
                files.append(FileInfo(parts[0], int(parts[1]), float(parts[2])))
        return sorted(files, key=lambda f: f.path)
