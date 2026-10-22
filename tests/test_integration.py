"""End-to-end: execute the real example notebooks with real py5 kernels on Xvfb.

Skipped unless a JDK 17+, Xvfb and py5 are available. The kernelspecs are
written to a temporary JUPYTER_PATH so nothing outside tmp_path is touched.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from jupyterlab_py5 import cli
from jupyterlab_py5.java import find_java, java_version_output, parse_java_version

ROOT = Path(__file__).resolve().parents[1]


def _java_ok() -> bool:
    import os

    java = find_java(os.environ)
    if not java:
        return False
    major = parse_java_version(java_version_output(java))
    return major is not None and major >= 17


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.skipif(importlib.util.find_spec("py5") is None, reason="py5 not installed"),
    pytest.mark.skipif(not _java_ok(), reason="no Java 17+"),
]


@pytest.fixture
def isolated_kernels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    kernels = tmp_path / "jupyter" / "kernels"
    for name, module in [
        ("py5", "py5jupyter.kernels.py5"),
        ("py5bot", "py5jupyter.kernels.py5bot"),
        ("python3", "ipykernel_launcher"),
    ]:
        (kernels / name).mkdir(parents=True)
        spec = {
            "argv": [sys.executable, "-m", module, "-f", "{connection_file}"],
            "display_name": name,
            "language": "python",
        }
        (kernels / name / "kernel.json").write_text(json.dumps(spec))
    monkeypatch.setenv("JUPYTER_PATH", str(tmp_path / "jupyter"))
    monkeypatch.setenv("JUPYTER_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path / "ipython"))
    monkeypatch.delenv("DISPLAY", raising=False)  # force the Xvfb path
    return kernels


def test_example_notebooks_execute_and_render(
    isolated_kernels: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    images = tmp_path / "frames"
    work = tmp_path / "notebooks"
    shutil.copytree(ROOT / "notebooks", work)
    code = cli.main(["run", str(work), "--timeout", "120", "--images-dir", str(images)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "3/3 notebook(s) passed" in out
    names = sorted(p.name for p in images.iterdir())
    assert any(n.startswith("01-interactive-sketch") for n in names)
    assert sum(n.startswith("02-static-drawing-py5bot") for n in names) == 2
    assert sum(n.startswith("03-module-mode-render") for n in names) == 2
    for png in images.iterdir():
        assert png.read_bytes().startswith(b"\x89PNG")


def test_with_display_runs_a_module_mode_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    out = tmp_path / "frame.png"
    script = tmp_path / "sketch.py"
    script.write_text(
        "import sys\n"
        "import py5\n"
        "def draw(s):\n"
        "    s.background(255, 0, 0)\n"
        "img = py5.render_frame(draw, 64, 48)\n"
        "img.save(sys.argv[1])\n"
    )
    code = cli.main(["with-display", "--", sys.executable, str(script), str(out)])
    assert code == 0
    from PIL import Image

    with Image.open(out) as img:
        assert img.size == (64, 48)
        assert img.convert("RGB").getpixel((10, 10)) == (255, 0, 0)


def test_new_notebook_executes(isolated_kernels: Path, tmp_path: Path, capsys) -> None:
    for kernel in ("py5", "py5bot", "python3"):
        argv = ["new", f"scaffold-{kernel}", "--kernel", kernel, "--dir", str(tmp_path)]
        assert cli.main(argv) == 0
    capsys.readouterr()
    code = cli.main(["run", str(tmp_path), "--timeout", "120"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "3/3 notebook(s) passed" in out
