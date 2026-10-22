from __future__ import annotations

import base64
from pathlib import Path
from typing import ClassVar

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

from jupyterlab_py5.notebooks import (
    KERNELS,
    clear_outputs,
    discover_notebooks,
    execute_notebook,
    extract_images,
    has_outputs,
    notebook_path,
    slugify,
    write_notebook,
)
from jupyterlab_py5.notebooks import new_notebook as scaffold

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.mark.parametrize("kernel", sorted(KERNELS))
def test_scaffold_is_valid_for_every_kernel(kernel: str) -> None:
    nb = scaffold(kernel, title="My sketch", width=640, height=360)
    nbformat.validate(nb)
    assert nb.metadata.kernelspec.name == kernel
    assert nb.cells[0].cell_type == "markdown"
    code = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert "640" in code and "360" in code
    compile(code, "<scaffold>", "exec")  # valid Python


def test_scaffold_without_title_has_only_code() -> None:
    nb = scaffold("py5")
    assert all(c.cell_type == "code" for c in nb.cells)
    assert "run_sketch()" in nb.cells[0].source


def test_scaffold_rejects_unknown_kernel_and_bad_size() -> None:
    with pytest.raises(ValueError, match="unknown kernel"):
        scaffold("julia")
    with pytest.raises(ValueError, match="positive"):
        scaffold("py5", width=0)


@pytest.mark.parametrize(
    ("name", "slug"),
    [("spiral study", "spiral-study"), ("  a/b\\c  ", "a-b-c"), ("ok_name-1.2", "ok_name-1.2")],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_slugify_rejects_empty() -> None:
    with pytest.raises(ValueError):
        slugify(" /// ")


def test_notebook_path(tmp_path: Path) -> None:
    assert notebook_path(tmp_path, "spiral study") == tmp_path / "spiral-study.ipynb"
    assert notebook_path(tmp_path, "x.ipynb") == tmp_path / "x.ipynb"


def test_write_notebook_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "a.ipynb"
    write_notebook(scaffold("py5bot"), path)
    assert nbformat.read(path, as_version=4).metadata.kernelspec.name == "py5bot"
    with pytest.raises(FileExistsError):
        write_notebook(scaffold("py5"), path)
    write_notebook(scaffold("py5"), path, overwrite=True)
    assert nbformat.read(path, as_version=4).metadata.kernelspec.name == "py5"


def test_discover_notebooks(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / ".ipynb_checkpoints").mkdir()
    for rel in ["a.ipynb", "b/c.ipynb", ".ipynb_checkpoints/a-checkpoint.ipynb", "notes.txt"]:
        (tmp_path / rel).write_text("{}")
    found = discover_notebooks([tmp_path, tmp_path / "a.ipynb"])
    assert found == [tmp_path / "a.ipynb", tmp_path / "b" / "c.ipynb"]


def test_discover_rejects_non_notebooks(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("")
    with pytest.raises(FileNotFoundError):
        discover_notebooks([tmp_path / "x.txt"])
    with pytest.raises(FileNotFoundError):
        discover_notebooks([tmp_path / "missing.ipynb"])


def _executed_notebook() -> nbformat.NotebookNode:
    nb = new_notebook()
    png = base64.b64encode(PNG).decode()
    nb.cells = [
        new_markdown_cell("hi"),
        new_code_cell(
            "a",
            execution_count=1,
            outputs=[
                new_output("execute_result", data={"image/png": png, "text/plain": "img"}),
                new_output("stream", name="stdout", text="x"),
            ],
        ),
        new_code_cell("b", execution_count=2, outputs=[]),
        new_code_cell(
            "c", execution_count=3, outputs=[new_output("display_data", data={"image/png": png})]
        ),
    ]
    return nb


def test_extract_images() -> None:
    images = extract_images(_executed_notebook(), "demo")
    assert [name for name, _ in images] == ["demo-cell02-01.png", "demo-cell04-02.png"]
    assert all(data == PNG for _, data in images)


def test_clear_outputs() -> None:
    nb = _executed_notebook()
    assert has_outputs(nb)
    assert clear_outputs(nb) is True
    assert not has_outputs(nb)
    assert all(c.get("execution_count") is None for c in nb.cells if c.cell_type == "code")
    assert clear_outputs(nb) is False
    nbformat.validate(nb)


class FakeClient:
    calls: ClassVar[list[dict]] = []

    def __init__(self, nb, error: Exception | None = None, **kwargs) -> None:
        self.nb = nb
        self.error = error
        FakeClient.calls.append(kwargs)

    def execute(self):
        if self.error:
            raise self.error
        self.nb.cells[0].outputs = [new_output("stream", name="stdout", text="done")]
        return self.nb


def test_execute_notebook_success(tmp_path: Path) -> None:
    path = tmp_path / "n.ipynb"
    write_notebook(scaffold("python3"), path)
    FakeClient.calls.clear()
    nb, result = execute_notebook(path, timeout=9, kernel_name="py5", client_factory=FakeClient)
    assert result.ok and result.error == ""
    assert nb.cells[0].outputs[0].text == "done"
    (kwargs,) = FakeClient.calls
    assert kwargs["timeout"] == 9
    assert kwargs["kernel_name"] == "py5"
    assert kwargs["resources"]["metadata"]["path"] == str(tmp_path)


def test_execute_notebook_failure_is_captured(tmp_path: Path) -> None:
    path = tmp_path / "n.ipynb"
    write_notebook(scaffold("python3"), path)

    def failing(nb, **kwargs):
        return FakeClient(nb, error=RuntimeError("trace\nZeroDivisionError: division by zero\n"))

    _, result = execute_notebook(path, client_factory=failing)
    assert not result.ok
    assert result.error == "RuntimeError: ZeroDivisionError: division by zero"
