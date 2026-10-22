"""Building the ``jupyter lab`` command line and the health check against it."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8888
DEFAULT_IP = "127.0.0.1"


@dataclass(frozen=True)
class LabSettings:
    ip: str = DEFAULT_IP
    port: int = DEFAULT_PORT
    root_dir: Path | None = None
    allow_root: bool = False
    extra_args: tuple[str, ...] = field(default_factory=tuple)


def parse_port(value: str | int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid port {value!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port


def settings_from_env(env: Mapping[str, str]) -> LabSettings:
    """Defaults from the environment: ``PY5_LAB_IP``, ``PY5_LAB_PORT``, ``PY5_LAB_ROOT``.

    The token is deliberately not handled here: Jupyter reads ``JUPYTER_TOKEN``
    itself and generates a random one when it is unset or empty, and keeping it
    out of argv keeps it out of ``ps``.
    """
    root = env.get("PY5_LAB_ROOT", "").strip()
    return LabSettings(
        ip=env.get("PY5_LAB_IP", "").strip() or DEFAULT_IP,
        port=parse_port(env.get("PY5_LAB_PORT", "").strip() or DEFAULT_PORT),
        root_dir=Path(root) if root else None,
    )


def build_lab_command(settings: LabSettings, python: str = sys.executable) -> list[str]:
    cmd = [
        python,
        "-m",
        "jupyterlab",
        f"--ServerApp.ip={settings.ip}",
        f"--ServerApp.port={settings.port}",
        "--ServerApp.open_browser=False",
    ]
    if settings.root_dir is not None:
        cmd.append(f"--ServerApp.root_dir={settings.root_dir}")
    if settings.allow_root:
        cmd.append("--ServerApp.allow_root=True")
    cmd.extend(settings.extra_args)
    return cmd


def running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def health_url(env: Mapping[str, str], port: int | None = None) -> str:
    """``/api`` answers without authentication, so it works as a liveness probe."""
    port = port or parse_port(env.get("PY5_LAB_PORT", "").strip() or DEFAULT_PORT)
    return f"http://127.0.0.1:{port}/api"


def check_health(
    url: str,
    *,
    timeout: float = 5.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[bool, str]:
    try:
        with opener(url, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read(512).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{url}: {exc}"
    if status != 200:
        return False, f"{url}: HTTP {status}"
    return True, f"{url}: {body.strip()}"


def split_passthrough(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split ``[own args] -- [passthrough]``."""
    if "--" in argv:
        index = list(argv).index("--")
        return list(argv[:index]), list(argv[index + 1 :])
    return list(argv), []
