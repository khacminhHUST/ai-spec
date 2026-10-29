"""Fixture Docker thật: skip nếu Docker chưa chạy hoặc chưa build image (sandbox/build.ps1)."""
import uuid

import pytest

from app.services.agent.config import agent_cfg


@pytest.fixture(scope="module")
def sbx_cfg():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get(agent_cfg()["sandbox"]["image"])
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Docker/image sandbox chưa sẵn sàng: {e}")
    return {**agent_cfg()["sandbox"], "backend": "docker"}


@pytest.fixture(scope="module")
def sbx(sbx_cfg):
    from app.services.agent.sandbox.docker_backend import DockerSandbox

    box = DockerSandbox(f"test-{uuid.uuid4().hex[:8]}", sbx_cfg)
    box.create()
    yield box
    box.destroy()


def py(box, code: str, timeout_s: int = 60):
    """Chạy code Python trong sandbox (qua put_file + exec như run_code)."""
    path = box.put_file(f"/workspace/.run/t_{uuid.uuid4().hex[:6]}.py", code.encode("utf-8"))
    return box.exec(["python", path], timeout_s=timeout_s)
