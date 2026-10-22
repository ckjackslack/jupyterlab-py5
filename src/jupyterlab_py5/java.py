"""Locating a Java runtime and reading its version (py5 needs Java 17 or newer)."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

MIN_JAVA = 17

_VERSION_RE = re.compile(r'version\s+"(?P<version>[^"]+)"')


def parse_java_version(output: str) -> int | None:
    """Return the major version from ``java -version`` output.

    Handles the legacy ``1.8.0_292`` scheme as well as ``17.0.9``, ``21`` and
    ``25-ea``. Noise such as "Picked up JAVA_TOOL_OPTIONS" lines is ignored.
    """
    match = _VERSION_RE.search(output)
    if not match:
        return None
    version = match.group("version")
    parts = re.split(r"[._+-]", version)
    try:
        major = int(parts[0])
        if major == 1 and len(parts) > 1:
            major = int(parts[1])
    except ValueError:
        return None
    return major


def find_java(
    env: Mapping[str, str],
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """Prefer ``$JAVA_HOME/bin/java`` (what JPype uses), else ``java`` on PATH."""
    java_home = env.get("JAVA_HOME", "").strip()
    if java_home:
        candidate = Path(java_home) / "bin" / "java"
        if candidate.is_file():
            return str(candidate)
    return which("java")


def java_version_output(java: str, timeout: float = 30.0) -> str:
    """Run ``java -version`` and return its combined output (it prints to stderr)."""
    result = subprocess.run(
        [java, "-version"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.stderr + result.stdout
