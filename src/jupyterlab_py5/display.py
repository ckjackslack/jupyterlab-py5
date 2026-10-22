"""Virtual X display management.

py5 drives Processing through Java AWT, and refuses to import without a display
("py5 is unable to run correctly in headless mode"). Inside a container (or on a
CI box) there is no X server, so we start an Xvfb instance on demand and point
``DISPLAY`` at it. When ``DISPLAY`` is already set (a desktop session, or X11
forwarding into the container) we leave it alone.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

DEFAULT_SCREEN = "1280x1024x24"
SCREEN_ENV = "PY5_SCREEN"

_SCREEN_RE = re.compile(r"^(\d{2,5})x(\d{2,5})x(8|16|24|32)$")


class DisplayError(RuntimeError):
    """Raised when no display is available and a virtual one cannot be started."""


@dataclass(frozen=True)
class Screen:
    width: int
    height: int
    depth: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}x{self.depth}"


def parse_screen(spec: str) -> Screen:
    """Parse an Xvfb screen spec such as ``1280x1024x24``."""
    match = _SCREEN_RE.match(spec.strip())
    if not match:
        raise ValueError(
            f"invalid screen spec {spec!r}: expected WIDTHxHEIGHTxDEPTH, e.g. {DEFAULT_SCREEN}"
        )
    width, height, depth = (int(g) for g in match.groups())
    return Screen(width, height, depth)


def needs_virtual_display(env: Mapping[str, str]) -> bool:
    """True when there is no X display to talk to (Java AWT needs X, not Wayland)."""
    return not env.get("DISPLAY", "").strip()


def xvfb_command(xvfb: str, screen: Screen, displayfd: int) -> list[str]:
    """Command line for an Xvfb that picks a free display number itself.

    ``-displayfd`` makes Xvfb choose an unused display and write its number to the
    given file descriptor once it is ready to accept connections, which avoids both
    races on ``:99`` and polling for the socket.
    """
    return [
        xvfb,
        "-displayfd",
        str(displayfd),
        "-screen",
        "0",
        str(screen),
        "-nolisten",
        "tcp",
        "-noreset",
    ]


def read_display_number(fd: int, timeout: float) -> int:
    """Read the display number Xvfb writes to ``fd`` (terminated by a newline)."""
    deadline = time.monotonic() + timeout
    buf = b""
    while not buf.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DisplayError(f"Xvfb did not report a display within {timeout:g}s")
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(fd, 64)
        if not chunk:
            raise DisplayError("Xvfb exited before reporting a display number")
        buf += chunk
    text = buf.decode("ascii", "replace").strip()
    if not text.isdigit():
        raise DisplayError(f"unexpected display number from Xvfb: {text!r}")
    return int(text)


class VirtualDisplay:
    """Context manager running an Xvfb server for the duration of the block."""

    def __init__(
        self,
        screen: Screen | str = DEFAULT_SCREEN,
        *,
        xvfb: str | None = None,
        startup_timeout: float = 15.0,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.screen = parse_screen(screen) if isinstance(screen, str) else screen
        self.startup_timeout = startup_timeout
        resolved = xvfb or which("Xvfb")
        if not resolved:
            raise DisplayError(
                "no DISPLAY is set and Xvfb is not installed; install it "
                "(Debian/Ubuntu: apt install xvfb) or set DISPLAY to a running X server"
            )
        self.xvfb = resolved
        self.display: str | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> str:
        read_fd, write_fd = os.pipe()
        try:
            self._proc = subprocess.Popen(
                xvfb_command(self.xvfb, self.screen, write_fd),
                pass_fds=(write_fd,),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            os.close(write_fd)
            write_fd = -1
            number = read_display_number(read_fd, self.startup_timeout)
        except BaseException:
            self.stop()
            raise
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            os.close(read_fd)
        self.display = f":{number}"
        return self.display

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def __enter__(self) -> VirtualDisplay:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


@contextmanager
def ensure_display(
    env: Mapping[str, str] | None = None,
    *,
    screen: str | None = None,
    factory: Callable[[str], VirtualDisplay] = VirtualDisplay,
) -> Iterator[dict[str, str]]:
    """Yield an environment that has a usable ``DISPLAY``.

    Starts (and afterwards stops) a virtual display only when ``env`` has none.
    """
    base = dict(os.environ if env is None else env)
    if not needs_virtual_display(base):
        yield base
        return
    spec = screen or base.get(SCREEN_ENV) or DEFAULT_SCREEN
    with factory(spec) as vdisplay:
        yield {**base, "DISPLAY": vdisplay.display or ""}


@contextmanager
def patched_environ(env: Mapping[str, str]) -> Iterator[None]:
    """Temporarily replace ``os.environ`` (kernels started by nbclient inherit it)."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def run_with_display(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    screen: str | None = None,
) -> int:
    """Run a command with a display available; forward SIGINT/SIGTERM to it."""
    if not argv:
        raise ValueError("no command given")
    with ensure_display(env, screen=screen) as child_env:
        proc = subprocess.Popen(list(argv), env=child_env)

        def forward(signum: int, _frame: object) -> None:
            proc.send_signal(signum)

        previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            return proc.wait()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
