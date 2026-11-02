"""Integration – bảo mật sandbox (docs/agent-harness.md mục 10.9)."""
import json
import os

import pytest

from tests.agent.integration.sandbox.conftest import py

pytestmark = pytest.mark.integration

NETWORK_CODE = '''
import socket, urllib.request
res = []
for target in [("8.8.8.8", 53), ("1.1.1.1", 443)]:
    try:
        socket.create_connection(target, timeout=3); res.append("connected")
    except OSError as e:
        res.append(type(e).__name__)
try:
    urllib.request.urlopen("https://pypi.org", timeout=3); res.append("connected")
except Exception as e:
    res.append(type(e).__name__)
try:
    socket.getaddrinfo("host.docker.internal", 80); res.append("resolved")
except OSError as e:
    res.append(type(e).__name__)
print(res)
'''

FORK_BOMB = '''
import os, time
n = 0
for _ in range(5000):
    try:
        if os.fork() == 0:
            time.sleep(60); os._exit(0)
        n += 1
    except OSError:
        print("blocked_at", n, flush=True); break
'''


def test_network_blocked(sbx):
    res = py(sbx, NETWORK_CODE)
    assert res.exit_code == 0, res.stderr
    assert "connected" not in res.stdout and "resolved" not in res.stdout


@pytest.mark.parametrize("path", ["/etc/x", "/x", "/home/x", "/opt/mep/templates/x",
                                  "/usr/local/lib/python3.11/site-packages/mep_helpers/x.py"])
def test_write_outside_workspace_tmp_fails(sbx, path):
    res = py(sbx, f"open({path!r}, 'w').write('x')")
    assert res.exit_code != 0 and ("Read-only file system" in res.stderr or "Permission denied" in res.stderr)


def test_write_workspace_and_tmp_ok_and_not_root(sbx):
    res = py(sbx, "import os\nopen('/tmp/a','w').write('1')\nopen('/workspace/output/a.txt','w').write('1')\n"
                  "print(os.getuid(), os.getgid())")
    assert res.exit_code == 0 and res.stdout.split() == ["1000", "1000"]


def test_infinite_loop_times_out_and_sandbox_recovers(sbx):
    sbx.put_file("output/keep.txt", b"keep")
    res = py(sbx, "while True: pass", timeout_s=3)
    assert res.timed_out
    after = py(sbx, "print(open('/workspace/output/keep.txt').read())")
    assert after.exit_code == 0 and after.stdout.strip() == "keep"     # file trong /workspace còn nguyên


def test_fork_bomb_blocked_by_pids(sbx):
    res = py(sbx, FORK_BOMB, timeout_s=10)
    assert "blocked_at" in res.stdout, (res.stdout, res.stderr)
    assert int(res.stdout.split()[-1]) < sbx.cfg["pids_limit"]
    alive = py(sbx, "print('alive')")                  # tiến trình còn sót được dọn, sandbox dùng tiếp được
    assert alive.exit_code == 0 and "alive" in alive.stdout


PUBLIC_IMAGE_ENV = {"GPG_KEY"}    # ID khóa GPG công khai của image python:3.11-slim (kiểm chứng mã nguồn Python)


def test_no_secret_env(sbx):
    from app.core.config import get_settings

    s = get_settings()
    secrets = [v for v in (s.google_api_key, s.deepinfra_api_key, s.postgres_url, s.qdrant_url) if v]
    res = py(sbx, "import os, json\nprint(json.dumps(dict(os.environ)))")
    env = json.loads(res.stdout)
    for name in set(env) - PUBLIC_IMAGE_ENV:
        assert not any(s in name.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "POSTGRES", "QDRANT")), name
    assert secrets and not any(v in res.stdout for v in secrets)
    assert not any(k in env for k in ("GOOGLE_API_KEY", "DEEPINFRA_API_KEY", "POSTGRES_URL", "QDRANT_URL"))
    assert os.environ.get("GOOGLE_API_KEY") or s.google_api_key   # key có trên host nhưng không lọt vào sandbox


def test_memory_limited(sbx):
    res = py(sbx, "x = bytearray(3 * 1024**3)\nx[::4096] = b'1' * len(x[::4096])\nprint('allocated')", timeout_s=30)
    assert "allocated" not in res.stdout
