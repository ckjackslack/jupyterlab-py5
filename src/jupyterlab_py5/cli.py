"""``jupyterlab-py5`` command line."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import nbformat

from . import __version__
from .display import SCREEN_ENV, DisplayError, ensure_display, parse_screen, patched_environ
from .display import run_with_display as _run_with_display
from .doctor import exit_code, format_report, run_checks
from .lab import (
    build_lab_command,
    check_health,
    health_url,
    parse_port,
    running_as_root,
    settings_from_env,
    split_passthrough,
)
from .notebooks import (
    KERNELS,
    NB_VERSION,
    clear_outputs,
    discover_notebooks,
    execute_notebook,
    extract_images,
    has_outputs,
    new_notebook,
    notebook_path,
    write_notebook,
)

EPILOG = """\
examples:
  jupyterlab-py5 lab                         start JupyterLab (Xvfb is started if DISPLAY is unset)
  jupyterlab-py5 run notebooks/ --images-dir frames/
  jupyterlab-py5 new "spiral study" --kernel py5 --size 600x600
  jupyterlab-py5 with-display -- python my_sketch.py
  jupyterlab-py5 doctor
"""


def _screen_arg(value: str) -> str:
    try:
        return str(parse_screen(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _size_arg(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid size {value!r}, expected WIDTHxHEIGHT") from None
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("size must be positive")
    return width, height


def _port_arg(value: str) -> int:
    try:
        return parse_port(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jupyterlab-py5",
        description="Run py5 (Processing for Python) sketches in JupyterLab, with a virtual "
        "display when no X server is available.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    screen_help = f"Xvfb screen WxHxDEPTH when one is started (env {SCREEN_ENV})"

    lab = sub.add_parser(
        "lab",
        help="start JupyterLab with a display for py5",
        description="Start JupyterLab. Extra Jupyter options go after '--'. "
        "Defaults come from PY5_LAB_IP, PY5_LAB_PORT, PY5_LAB_ROOT; "
        "the token from JUPYTER_TOKEN (random if unset).",
    )
    lab.add_argument("--ip", help="interface to listen on (default 127.0.0.1)")
    lab.add_argument("--port", type=_port_arg, help="port (default 8888)")
    lab.add_argument("--root-dir", type=Path, help="notebook directory")
    lab.add_argument("--screen", type=_screen_arg, help=screen_help)
    lab.add_argument("--dry-run", action="store_true", help="print the command and exit")

    run = sub.add_parser(
        "run",
        help="execute notebooks headlessly (CI smoke test, frame export)",
        description="Execute notebooks top to bottom with nbclient. Exit status 1 if any fails.",
    )
    run.add_argument("paths", nargs="+", type=Path, help="notebooks or directories")
    run.add_argument("--timeout", type=_positive_float, default=120, help="per-cell seconds")
    run.add_argument("--kernel", help="override the kernel named in each notebook")
    run.add_argument("--output-dir", type=Path, help="write executed notebooks here")
    run.add_argument("--images-dir", type=Path, help="save every PNG the notebooks render here")
    run.add_argument("--screen", type=_screen_arg, help=screen_help)

    new = sub.add_parser("new", help="create a starter sketch notebook")
    new.add_argument("name", help="notebook name (becomes NAME.ipynb)")
    new.add_argument("--kernel", choices=sorted(KERNELS), default="py5")
    new.add_argument("--size", type=_size_arg, default=(400, 400), help="WIDTHxHEIGHT")
    new.add_argument("--dir", type=Path, default=Path(), help="target directory (default .)")
    new.add_argument("--force", action="store_true", help="overwrite an existing notebook")

    clean = sub.add_parser("clean", help="strip outputs from notebooks before committing")
    clean.add_argument("paths", nargs="+", type=Path, help="notebooks or directories")
    clean.add_argument(
        "--check", action="store_true", help="only report; exit 1 if any notebook has outputs"
    )

    sub.add_parser("doctor", help="check Java, py5, kernels and display")

    wd = sub.add_parser(
        "with-display",
        help="run any command with a display (e.g. a .py sketch)",
        description="Run COMMAND (after '--') with DISPLAY set, starting Xvfb if needed.",
    )
    wd.add_argument("--screen", type=_screen_arg, help=screen_help)

    health = sub.add_parser("healthcheck", help="exit 0 if the local JupyterLab answers")
    health.add_argument("--url", help="URL to probe (default http://127.0.0.1:$PY5_LAB_PORT/api)")
    health.add_argument("--timeout", type=_positive_float, default=5.0)

    return parser


def _err(message: str) -> None:
    print(f"jupyterlab-py5: error: {message}", file=sys.stderr)


def cmd_lab(args: argparse.Namespace, extra: list[str]) -> int:
    base = settings_from_env(os.environ)
    settings = type(base)(
        ip=args.ip or base.ip,
        port=args.port or base.port,
        root_dir=args.root_dir or base.root_dir,
        allow_root=running_as_root(),
        extra_args=tuple(extra),
    )
    cmd = build_lab_command(settings)
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    return _run_with_display(cmd, screen=args.screen)


def cmd_run(args: argparse.Namespace) -> int:
    notebooks = discover_notebooks(args.paths)
    if not notebooks:
        _err("no notebooks found")
        return 2
    failures = 0
    with ensure_display(screen=args.screen) as env, patched_environ(env):
        print(f"DISPLAY={env.get('DISPLAY')}", file=sys.stderr)
        for path in notebooks:
            nb, result = execute_notebook(path, timeout=args.timeout, kernel_name=args.kernel)
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                nbformat.write(nb, args.output_dir / path.name)
            if args.images_dir:
                args.images_dir.mkdir(parents=True, exist_ok=True)
                for name, data in extract_images(nb, path.stem):
                    target = args.images_dir / name
                    target.write_bytes(data)
                    result.images.append(target)
            status = "ok  " if result.ok else "FAIL"
            extra = f", {len(result.images)} image(s)" if args.images_dir else ""
            print(f"[{status}] {path} ({result.seconds:.1f}s{extra})")
            if not result.ok:
                failures += 1
                print(f"       {result.error}")
    print(f"{len(notebooks) - failures}/{len(notebooks)} notebook(s) passed")
    return 1 if failures else 0


def cmd_new(args: argparse.Namespace) -> int:
    width, height = args.size
    path = notebook_path(args.dir, args.name)
    nb = new_notebook(args.kernel, title=args.name, width=width, height=height)
    write_notebook(nb, path, overwrite=args.force)
    print(path)
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    dirty = 0
    for path in discover_notebooks(args.paths):
        nb = nbformat.read(path, as_version=NB_VERSION)
        if args.check:
            if has_outputs(nb):
                dirty += 1
                print(f"has outputs: {path}")
        elif clear_outputs(nb):
            nbformat.write(nb, path)
            dirty += 1
            print(f"cleaned: {path}")
    if args.check:
        return 1 if dirty else 0
    if not dirty:
        print("nothing to clean")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks = run_checks()
    print(format_report(checks))
    return exit_code(checks)


def cmd_with_display(args: argparse.Namespace, extra: list[str]) -> int:
    if not extra:
        _err("no command given; usage: jupyterlab-py5 with-display -- COMMAND [ARGS...]")
        return 2
    return _run_with_display(extra, screen=args.screen)


def cmd_healthcheck(args: argparse.Namespace) -> int:
    url = args.url or health_url(os.environ)
    ok, message = check_health(url, timeout=args.timeout)
    print(message)
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    own, extra = split_passthrough(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(own)
    if extra and args.command not in {"lab", "with-display"}:
        parser.error("'--' arguments are only accepted by 'lab' and 'with-display'")
    try:
        if args.command == "lab":
            return cmd_lab(args, extra)
        if args.command == "with-display":
            return cmd_with_display(args, extra)
        handler = {
            "run": cmd_run,
            "new": cmd_new,
            "clean": cmd_clean,
            "doctor": cmd_doctor,
            "healthcheck": cmd_healthcheck,
        }[args.command]
        return handler(args)
    except (DisplayError, FileNotFoundError, FileExistsError, ValueError) as exc:
        _err(str(exc))
        return 2
    except nbformat.ValidationError as exc:
        _err(f"invalid notebook: {exc.message}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
