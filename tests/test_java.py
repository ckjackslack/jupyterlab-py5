from __future__ import annotations

from pathlib import Path

import pytest

from jupyterlab_py5.java import find_java, parse_java_version


@pytest.mark.parametrize(
    ("output", "major"),
    [
        ('openjdk version "21.0.12.1" 2026-08-18\nOpenJDK Runtime Environment', 21),
        ('openjdk version "17.0.9" 2023-10-17', 17),
        ('java version "1.8.0_292"', 8),
        ('openjdk version "25" 2025-09-16', 25),
        ('openjdk version "26-ea" 2026-03-17', 26),
        ('Picked up JAVA_TOOL_OPTIONS: -Dfoo="bar"\nopenjdk version "21.0.2" 2024-01-16', 21),
        ("no version here", None),
        ('openjdk version "abc"', None),
    ],
)
def test_parse_java_version(output: str, major: int | None) -> None:
    assert parse_java_version(output) == major


def test_find_java_prefers_java_home(tmp_path: Path) -> None:
    java = tmp_path / "bin" / "java"
    java.parent.mkdir()
    java.write_text("")
    assert find_java({"JAVA_HOME": str(tmp_path)}, which=lambda _n: "/usr/bin/java") == str(java)


def test_find_java_falls_back_to_path(tmp_path: Path) -> None:
    # JAVA_HOME pointing somewhere without bin/java is ignored.
    assert find_java({"JAVA_HOME": str(tmp_path)}, which=lambda _n: "/usr/bin/java") == (
        "/usr/bin/java"
    )
    assert find_java({}, which=lambda _n: None) is None
