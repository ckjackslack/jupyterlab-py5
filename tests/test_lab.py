from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import pytest

from jupyterlab_py5.lab import (
    LabSettings,
    build_lab_command,
    check_health,
    health_url,
    parse_port,
    settings_from_env,
    split_passthrough,
)


def test_settings_defaults() -> None:
    assert settings_from_env({}) == LabSettings(ip="127.0.0.1", port=8888, root_dir=None)


def test_settings_from_env() -> None:
    s = settings_from_env({"PY5_LAB_IP": "0.0.0.0", "PY5_LAB_PORT": "9000", "PY5_LAB_ROOT": "/nb"})
    assert (s.ip, s.port, s.root_dir) == ("0.0.0.0", 9000, Path("/nb"))


@pytest.mark.parametrize("value", ["0", "65536", "http", "", "-1"])
def test_parse_port_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        parse_port(value)


def test_build_lab_command_minimal() -> None:
    cmd = build_lab_command(LabSettings(), python="/py")
    assert cmd[:3] == ["/py", "-m", "jupyterlab"]
    assert "--ServerApp.ip=127.0.0.1" in cmd
    assert "--ServerApp.port=8888" in cmd
    assert "--ServerApp.open_browser=False" in cmd
    assert not any("root_dir" in a or "allow_root" in a or "token" in a.lower() for a in cmd)


def test_build_lab_command_full() -> None:
    settings = LabSettings(
        ip="0.0.0.0",
        port=9999,
        root_dir=Path("/home/py5/notebooks"),
        allow_root=True,
        extra_args=("--LabApp.dev_mode=False",),
    )
    cmd = build_lab_command(settings, python="/py")
    assert "--ServerApp.root_dir=/home/py5/notebooks" in cmd
    assert "--ServerApp.allow_root=True" in cmd
    assert cmd[-1] == "--LabApp.dev_mode=False"
    # The original image passed --ip='*' in exec form; make sure nothing is quoted.
    assert not any("'" in arg for arg in cmd)


def test_health_url() -> None:
    assert health_url({}) == "http://127.0.0.1:8888/api"
    assert health_url({"PY5_LAB_PORT": "9000"}) == "http://127.0.0.1:9000/api"
    assert health_url({}, port=1234) == "http://127.0.0.1:1234/api"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status


def test_check_health_ok() -> None:
    seen = {}

    def opener(url, timeout):
        seen.update(url=url, timeout=timeout)
        return FakeResponse(b'{"version": "2.21.1"}')

    ok, message = check_health("http://x/api", timeout=2, opener=opener)
    assert ok
    assert "2.21.1" in message
    assert seen == {"url": "http://x/api", "timeout": 2}


def test_check_health_http_error_status() -> None:
    ok, message = check_health("http://x/api", opener=lambda u, timeout: FakeResponse(b"", 503))
    assert not ok and "503" in message


def test_check_health_connection_refused() -> None:
    def opener(url, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    ok, message = check_health("http://x/api", opener=opener)
    assert not ok and "refused" in message


@pytest.mark.parametrize(
    ("argv", "own", "extra"),
    [
        (["lab"], ["lab"], []),
        (["lab", "--", "--debug"], ["lab"], ["--debug"]),
        (["with-display", "--", "python", "--", "x"], ["with-display"], ["python", "--", "x"]),
    ],
)
def test_split_passthrough(argv, own, extra) -> None:
    assert split_passthrough(argv) == (own, extra)
