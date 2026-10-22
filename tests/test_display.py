from __future__ import annotations

import os
import shutil
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

import pytest

from jupyterlab_py5 import display
from jupyterlab_py5.display import (
    DisplayError,
    Screen,
    VirtualDisplay,
    ensure_display,
    needs_virtual_display,
    parse_screen,
    patched_environ,
    read_display_number,
    run_with_display,
    xvfb_command,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1280x1024x24", Screen(1280, 1024, 24)),
        (" 640x480x16 ", Screen(640, 480, 16)),
        ("3840x2160x32", Screen(3840, 2160, 32)),
    ],
)
def test_parse_screen(spec: str, expected: Screen) -> None:
    assert parse_screen(spec) == expected
    assert str(expected) == spec.strip()


@pytest.mark.parametrize("spec", ["", "1280x1024", "1280x1024x7", "axbxc", "1x1x24", "800X600X24"])
def test_parse_screen_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ValueError, match="invalid screen spec"):
        parse_screen(spec)


@pytest.mark.parametrize(
    ("env", "needed"),
    [
        ({}, True),
        ({"DISPLAY": ""}, True),
        ({"DISPLAY": "  "}, True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, True),  # AWT needs X (or XWayland's DISPLAY)
        ({"DISPLAY": ":0"}, False),
        ({"DISPLAY": "host:10.0"}, False),
    ],
)
def test_needs_virtual_display(env: dict[str, str], needed: bool) -> None:
    assert needs_virtual_display(env) is needed


def test_xvfb_command_uses_displayfd_and_no_tcp() -> None:
    cmd = xvfb_command("/usr/bin/Xvfb", Screen(800, 600, 24), 7)
    assert cmd[0] == "/usr/bin/Xvfb"
    assert cmd[cmd.index("-displayfd") + 1] == "7"
    assert cmd[cmd.index("-screen") + 2] == "800x600x24"
    assert cmd[cmd.index("-nolisten") + 1] == "tcp"


def _pipe_with(data: bytes, close: bool = True) -> int:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    if close:
        os.close(write_fd)
    return read_fd


def test_read_display_number() -> None:
    fd = _pipe_with(b"42\n")
    try:
        assert read_display_number(fd, timeout=1) == 42
    finally:
        os.close(fd)


def test_read_display_number_in_chunks() -> None:
    read_fd, write_fd = os.pipe()

    def writer() -> None:
        os.write(write_fd, b"1")
        os.write(write_fd, b"7\n")
        os.close(write_fd)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert read_display_number(read_fd, timeout=2) == 17
    finally:
        thread.join()
        os.close(read_fd)


def test_read_display_number_eof() -> None:
    fd = _pipe_with(b"")
    try:
        with pytest.raises(DisplayError, match="exited"):
            read_display_number(fd, timeout=1)
    finally:
        os.close(fd)


def test_read_display_number_garbage() -> None:
    fd = _pipe_with(b"oops\n")
    try:
        with pytest.raises(DisplayError, match="unexpected"):
            read_display_number(fd, timeout=1)
    finally:
        os.close(fd)


def test_read_display_number_timeout() -> None:
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(DisplayError, match="did not report"):
            read_display_number(read_fd, timeout=0.05)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_virtual_display_without_xvfb_explains_fix() -> None:
    with pytest.raises(DisplayError, match="apt install xvfb"):
        VirtualDisplay("640x480x24", which=lambda _name: None)


def test_virtual_display_rejects_bad_screen() -> None:
    with pytest.raises(ValueError):
        VirtualDisplay("big", which=lambda _name: "/usr/bin/Xvfb")


def test_virtual_display_startup_failure_is_reported(tmp_path: Path) -> None:
    # A fake "Xvfb" that exits immediately without writing a display number.
    fake = tmp_path / "Xvfb"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    vd = VirtualDisplay("640x480x24", xvfb=str(fake), startup_timeout=5)
    with pytest.raises(DisplayError, match="exited"):
        vd.start()
    assert vd._proc is None


def test_virtual_display_with_fake_xvfb(tmp_path: Path) -> None:
    # Fake Xvfb: report display 77 on the -displayfd descriptor, then idle.
    fake = tmp_path / "Xvfb"
    fake.write_text('#!/bin/bash\necho 77 >&"$2"\nexec sleep 30\n')
    fake.chmod(0o755)
    with VirtualDisplay("640x480x24", xvfb=str(fake)) as vd:
        assert vd.display == ":77"
        proc = vd._proc
        assert proc is not None and proc.poll() is None
    assert proc.poll() is not None  # stopped on exit


class FakeDisplay:
    instances: ClassVar[list[FakeDisplay]] = []

    def __init__(self, screen: str) -> None:
        self.screen = screen
        self.display: str | None = None
        self.stopped = False
        FakeDisplay.instances.append(self)

    def __enter__(self) -> FakeDisplay:
        self.display = ":5"
        return self

    def __exit__(self, *exc: object) -> None:
        self.stopped = True


def test_ensure_display_keeps_existing_display() -> None:
    FakeDisplay.instances.clear()
    with ensure_display({"DISPLAY": ":1", "X": "y"}, factory=FakeDisplay) as env:
        assert env == {"DISPLAY": ":1", "X": "y"}
    assert FakeDisplay.instances == []


def test_ensure_display_starts_virtual_display() -> None:
    FakeDisplay.instances.clear()
    with ensure_display({"PY5_SCREEN": "800x600x24"}, factory=FakeDisplay) as env:
        assert env["DISPLAY"] == ":5"
        (fake,) = FakeDisplay.instances
        assert fake.screen == "800x600x24"
        assert not fake.stopped
    assert fake.stopped


def test_ensure_display_screen_argument_wins() -> None:
    FakeDisplay.instances.clear()
    with ensure_display({"PY5_SCREEN": "800x600x24"}, screen="640x480x8", factory=FakeDisplay):
        pass
    assert FakeDisplay.instances[0].screen == "640x480x8"


def test_ensure_display_default_screen() -> None:
    FakeDisplay.instances.clear()
    with ensure_display({}, factory=FakeDisplay):
        pass
    assert FakeDisplay.instances[0].screen == display.DEFAULT_SCREEN


def test_patched_environ_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEP_ME", "1")
    with patched_environ({"ONLY": "this"}):
        assert dict(os.environ) == {"ONLY": "this"}
    assert os.environ["KEEP_ME"] == "1"
    assert "ONLY" not in os.environ


def test_run_with_display_passes_display_and_exit_code() -> None:
    code = run_with_display(
        [sys.executable, "-c", "import os, sys; sys.exit(os.environ['DISPLAY'] != ':9')"],
        env={"DISPLAY": ":9", "PATH": os.environ.get("PATH", "")},
    )
    assert code == 0
    code = run_with_display([sys.executable, "-c", "raise SystemExit(3)"], env={"DISPLAY": ":9"})
    assert code == 3


def test_run_with_display_starts_display_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_ensure(env, *, screen=None):
        yield {**env, "DISPLAY": ":66"}

    monkeypatch.setattr(display, "ensure_display", fake_ensure)
    code = run_with_display(
        [sys.executable, "-c", "import os, sys; sys.exit(os.environ['DISPLAY'] != ':66')"],
        env={},
    )
    assert code == 0


def test_run_with_display_requires_command() -> None:
    with pytest.raises(ValueError):
        run_with_display([], env={"DISPLAY": ":1"})


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_real_xvfb_starts_and_stops() -> None:
    with VirtualDisplay("320x240x24") as vd:
        assert vd.display and vd.display.startswith(":")
        number = vd.display[1:]
        assert Path(f"/tmp/.X11-unix/X{number}").exists()
        proc = vd._proc
    assert proc is not None and proc.poll() is not None
