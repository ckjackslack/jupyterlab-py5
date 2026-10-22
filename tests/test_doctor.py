from __future__ import annotations

import pytest

from jupyterlab_py5.doctor import (
    Probes,
    Status,
    check_display,
    check_java,
    check_kernels,
    check_modules,
    check_python,
    exit_code,
    format_report,
    run_checks,
)

JAVA21 = 'openjdk version "21.0.2" 2024-01-16'


def healthy(**overrides) -> Probes:
    values = {
        "env": {"DISPLAY": ":0"},
        "python_version": (3, 13, 1),
        "which": lambda name: f"/usr/bin/{name}",
        "java_output": lambda _java: JAVA21,
        "has_module": lambda _name: True,
        "kernels": lambda: {"py5": "/k/py5", "py5bot": "/k/py5bot", "python3": "/k/python3"},
    }
    values.update(overrides)
    return Probes(**values)


def test_all_healthy() -> None:
    checks = run_checks(healthy())
    assert [c.status for c in checks] == [Status.OK] * 5
    assert exit_code(checks) == 0
    report = format_report(checks)
    assert "Java 21" in report
    assert "->" not in report  # no hints when everything is fine


def test_python_too_old() -> None:
    check = check_python((3, 10, 4))
    assert check.status is Status.FAIL
    assert "3.12" in check.hint


def test_java_missing() -> None:
    check = check_java(healthy(which=lambda _n: None))
    assert check.status is Status.FAIL
    assert "JAVA_HOME" in check.hint


def test_java_too_old() -> None:
    check = check_java(healthy(java_output=lambda _j: 'java version "1.8.0_292"'))
    assert check.status is Status.FAIL
    assert "Java 8" in check.detail


def test_java_unparseable_is_warning() -> None:
    check = check_java(healthy(java_output=lambda _j: "garbage"))
    assert check.status is Status.WARN


def test_java_broken_executable() -> None:
    def boom(_java: str) -> str:
        raise PermissionError("denied")

    check = check_java(healthy(java_output=boom))
    assert check.status is Status.FAIL
    assert "denied" in check.detail


def test_missing_modules_listed() -> None:
    check = check_modules(healthy(has_module=lambda name: name not in {"py5", "nbclient"}))
    assert check.status is Status.FAIL
    assert check.detail == "missing: py5, nbclient"


def test_missing_kernels_is_warning_with_install_hint() -> None:
    check = check_kernels(healthy(kernels=lambda: {"python3": "/k"}))
    assert check.status is Status.WARN
    assert "py5bot.install" in check.hint


def test_kernel_listing_error() -> None:
    def broken():
        raise RuntimeError("no jupyter")

    check = check_kernels(healthy(kernels=broken))
    assert check.status is Status.FAIL


@pytest.mark.parametrize(
    ("env", "which", "status"),
    [
        ({"DISPLAY": ":1"}, lambda _n: None, Status.OK),
        ({}, lambda _n: "/usr/bin/Xvfb", Status.OK),
        ({}, lambda _n: None, Status.FAIL),
    ],
)
def test_display(env, which, status) -> None:
    assert check_display(healthy(env=env, which=which)).status is status


def test_failures_set_exit_code_and_show_hints() -> None:
    checks = run_checks(healthy(env={}, which=lambda _n: None))
    assert exit_code(checks) == 1
    report = format_report(checks)
    assert "[FAIL] java" in report
    assert "install xvfb" in report


def test_warnings_do_not_fail() -> None:
    checks = run_checks(healthy(kernels=lambda: {}))
    assert exit_code(checks) == 0
