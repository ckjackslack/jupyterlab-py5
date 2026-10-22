"""Notebook helpers: scaffold new sketch notebooks, execute them headlessly,
export the frames they render, and strip outputs before committing."""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import nbformat
from nbformat import NotebookNode

NB_VERSION = 4

KERNELS: dict[str, str] = {
    "py5": "py5 (imported mode: setup()/draw() without the py5. prefix)",
    "py5bot": "py5bot (static drawing, each cell renders an image)",
    "python3": "plain Python kernel using py5 in module mode",
}

_TEMPLATES: dict[str, list[str]] = {
    "py5": [
        "def setup():\n"
        "    size({width}, {height})\n"
        "    rect_mode(CENTER)\n"
        "\n"
        "\n"
        "def draw():\n"
        "    background(240)\n"
        "    square(width / 2, height / 2, 20 + 10 * sin(frame_count / 20))\n"
        "\n"
        "\n"
        "run_sketch()",
        "# Grab the current frame from the running sketch (works without a visible window).\n"
        "import py5_tools\n"
        "\n"
        "py5_tools.screenshot()",
        "exit_sketch()",
    ],
    "py5bot": [
        "size({width}, {height})\n"
        "background(240)\n"
        "fill(255, 80, 80)\n"
        "circle(width / 2, height / 2, width / 2)",
    ],
    "python3": [
        "import py5\n"
        "\n"
        "\n"
        "@py5.render({width}, {height})\n"
        "def frame(s: py5.Sketch) -> None:\n"
        "    s.background(240)\n"
        "    s.fill(80, 80, 255)\n"
        "    s.circle(s.width / 2, s.height / 2, s.width / 2)\n"
        "\n"
        "\n"
        "frame()",
    ],
}


def kernelspec_metadata(kernel: str) -> dict[str, str]:
    return {"name": kernel, "display_name": kernel, "language": "python"}


def new_notebook(
    kernel: str = "py5", *, title: str | None = None, width: int = 400, height: int = 400
) -> NotebookNode:
    """Build a starter notebook for the given kernel."""
    if kernel not in _TEMPLATES:
        raise ValueError(f"unknown kernel {kernel!r}; choose from {', '.join(_TEMPLATES)}")
    if width <= 0 or height <= 0:
        raise ValueError("sketch size must be positive")
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = kernelspec_metadata(kernel)
    nb.metadata["language_info"] = {"name": "python"}
    cells = []
    if title:
        cells.append(nbformat.v4.new_markdown_cell(f"# {title}\n\nKernel: {KERNELS[kernel]}"))
    cells += [
        nbformat.v4.new_code_cell(src.format(width=width, height=height))
        for src in _TEMPLATES[kernel]
    ]
    nb.cells = cells
    return nb


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")
    if not slug:
        raise ValueError(f"cannot make a file name from {name!r}")
    return slug


def notebook_path(directory: Path, name: str) -> Path:
    stem = name[: -len(".ipynb")] if name.endswith(".ipynb") else name
    return directory / f"{slugify(stem)}.ipynb"


def write_notebook(nb: NotebookNode, path: Path, *, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.validate(nb)
    nbformat.write(nb, path)
    return path


def discover_notebooks(paths: Iterable[Path]) -> list[Path]:
    """Expand files and directories into a sorted, de-duplicated list of notebooks."""
    found: dict[Path, None] = {}
    for path in paths:
        if path.is_dir():
            for nb in sorted(path.rglob("*.ipynb")):
                if ".ipynb_checkpoints" not in nb.parts:
                    found[nb] = None
        elif path.suffix == ".ipynb" and path.is_file():
            found[path] = None
        else:
            raise FileNotFoundError(f"not a notebook or directory: {path}")
    return list(found)


def iter_outputs(nb: NotebookNode) -> Iterator[tuple[int, dict[str, Any]]]:
    for index, cell in enumerate(nb.cells):
        if cell.cell_type == "code":
            for output in cell.get("outputs", []):
                yield index, output


def extract_images(nb: NotebookNode, stem: str) -> list[tuple[str, bytes]]:
    """Return ``(filename, png_bytes)`` for every PNG a notebook rendered."""
    images = []
    for count, (cell_index, output) in enumerate(
        (item for item in iter_outputs(nb) if "image/png" in item[1].get("data", {})), start=1
    ):
        data = output["data"]["image/png"]
        if isinstance(data, list):
            data = "".join(data)
        images.append((f"{stem}-cell{cell_index + 1:02d}-{count:02d}.png", base64.b64decode(data)))
    return images


def has_outputs(nb: NotebookNode) -> bool:
    return any(
        cell.cell_type == "code" and (cell.get("outputs") or cell.get("execution_count"))
        for cell in nb.cells
    )


def clear_outputs(nb: NotebookNode) -> bool:
    """Strip outputs and execution counts in place; return True if anything changed."""
    changed = False
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        if cell.get("outputs"):
            cell.outputs = []
            changed = True
        if cell.get("execution_count") is not None:
            cell.execution_count = None
            changed = True
    return changed


@dataclass
class RunResult:
    path: Path
    ok: bool
    seconds: float
    error: str = ""
    images: list[Path] = field(default_factory=list)


ClientFactory = Callable[..., Any]


def _default_client_factory(nb: NotebookNode, **kwargs: Any) -> Any:
    from nbclient import NotebookClient

    return NotebookClient(nb, **kwargs)


def execute_notebook(
    path: Path,
    *,
    timeout: float = 120,
    kernel_name: str | None = None,
    client_factory: ClientFactory = _default_client_factory,
) -> tuple[NotebookNode, RunResult]:
    """Execute a notebook in its own directory and report success/failure.

    Errors raised by cells are captured into the result rather than propagated,
    so one broken notebook does not stop a batch run.
    """
    nb = nbformat.read(path, as_version=NB_VERSION)
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "resources": {"metadata": {"path": str(path.parent)}},
    }
    if kernel_name:
        kwargs["kernel_name"] = kernel_name
    start = time.monotonic()
    try:
        client_factory(nb, **kwargs).execute()
    except Exception as exc:
        message = str(exc).strip().splitlines()
        summary = next((line for line in reversed(message) if line.strip()), type(exc).__name__)
        return nb, RunResult(
            path, False, time.monotonic() - start, f"{type(exc).__name__}: {summary}"
        )
    return nb, RunResult(path, True, time.monotonic() - start)
