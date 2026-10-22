"""``jupyterlab-py5 doctor``: check that everything py5-in-Jupyter needs is present.

Every probe that touches the system is injectable, so the logic is testable
without a JDK, an X server or installed kernels.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from .java import MIN_JAVA, find_java, java_version_output, parse_java_version

REQUIRED_MODULES = ("py5", "py5_tools", "py5jupyter", "jupyterlab", "nbclient", "ipywidgets")
REQUIRED_KERNELS = ("py5", "py5bot")
MIN_PYTHON = (3, 12)


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""


def _default_kernels() -> Mapping[str, str]:
    from jupyter_client.kernelspec import KernelSpecManager

    return KernelSpecManager().find_kernel_specs()


@dataclass
class Probes:
    """System access used by the checks; replace any of these in tests."""

    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    python_version: tuple[int, ...] = tuple(sys.version_info[:3])
    which: Callable[[str], str | None] = shutil.which
    java_output: Callable[[str], str] = java_version_output
    has_module: Callable[[str], bool] = lambda name: importlib.util.find_spec(name) is not None
    kernels: Callable[[], Mapping[str, str]] = _default_kernels


def check_python(version: tuple[int, ...]) -> Check:
    text = ".".join(map(str, version))
    if tuple(version[:2]) >= MIN_PYTHON:
        return Check("python", Status.OK, text)
    need = ".".join(map(str, MIN_PYTHON))
    return Check("python", Status.FAIL, text, f"use Python {need} or newer")


def check_java(probes: Probes, minimum: int = MIN_JAVA) -> Check:
    java = find_java(probes.env, probes.which)
    if not java:
        return Check(
            "java",
            Status.FAIL,
            "no java executable found",
            f"install a JDK/JRE {minimum}+ and set JAVA_HOME (e.g. apt install openjdk-21-jre)",
        )
    try:
        output = probes.java_output(java)
    except OSError as exc:
        return Check("java", Status.FAIL, f"{java}: {exc}", "check the JAVA_HOME installation")
    major = parse_java_version(output)
    if major is None:
        return Check("java", Status.WARN, f"{java}: could not parse version", "")
    if major < minimum:
        return Check(
            "java", Status.FAIL, f"{java}: Java {major}", f"py5 needs Java {minimum} or newer"
        )
    return Check("java", Status.OK, f"{java}: Java {major}")


def check_modules(probes: Probes, names: Iterable[str] = REQUIRED_MODULES) -> Check:
    missing = [name for name in names if not probes.has_module(name)]
    if missing:
        return Check(
            "python packages",
            Status.FAIL,
            "missing: " + ", ".join(missing),
            "pip install jupyterlab-py5 (or: uv sync)",
        )
    return Check("python packages", Status.OK, "all importable")


def check_kernels(probes: Probes, required: Iterable[str] = REQUIRED_KERNELS) -> Check:
    try:
        installed = probes.kernels()
    except Exception as exc:  # jupyter_client missing or broken config
        return Check("kernels", Status.FAIL, f"cannot list kernels: {exc}")
    missing = [name for name in required if name not in installed]
    if missing:
        return Check(
            "kernels",
            Status.WARN,
            "missing: " + ", ".join(missing),
            "python -m py5jupyter.kernels.py5.install --sys-prefix "
            "&& python -m py5jupyter.kernels.py5bot.install --sys-prefix",
        )
    return Check("kernels", Status.OK, ", ".join(sorted(installed)))


def check_display(probes: Probes) -> Check:
    display = probes.env.get("DISPLAY", "").strip()
    if display:
        return Check("display", Status.OK, f"DISPLAY={display}")
    xvfb = probes.which("Xvfb")
    if xvfb:
        return Check("display", Status.OK, f"no DISPLAY; will start Xvfb ({xvfb})")
    return Check(
        "display",
        Status.FAIL,
        "no DISPLAY and no Xvfb",
        "py5 cannot import headless: install xvfb or set DISPLAY",
    )


def run_checks(probes: Probes | None = None) -> list[Check]:
    probes = probes or Probes()
    return [
        check_python(probes.python_version),
        check_java(probes),
        check_modules(probes),
        check_kernels(probes),
        check_display(probes),
    ]


def format_report(checks: Iterable[Check]) -> str:
    lines = []
    for check in checks:
        lines.append(f"[{check.status.value:>4}] {check.name:<16} {check.detail}")
        if check.hint and check.status is not Status.OK:
            lines.append(f"{'':7}{'':<16} -> {check.hint}")
    return "\n".join(lines)


def exit_code(checks: Iterable[Check]) -> int:
    return 1 if any(check.status is Status.FAIL for check in checks) else 0
