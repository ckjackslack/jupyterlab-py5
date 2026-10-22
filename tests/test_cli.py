from __future__ import annotations

import base64
import sys
from contextlib import contextmanager
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_output

from jupyterlab_py5 import __version__, cli
from jupyterlab_py5.doctor import Check, Status
from jupyterlab_py5.notebooks import RunResult


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_help_lists_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in ("lab", "run", "new", "clean", "doctor", "with-display", "healthcheck"):
        assert command in out


def test_version(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_missing_command_is_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_new_creates_notebook(tmp_path: Path, capsys) -> None:
    code, out, _ = run(capsys, "new", "spiral study", "--dir", str(tmp_path), "--size", "640x360")
    assert code == 0
    path = tmp_path / "spiral-study.ipynb"
    assert out.strip() == str(path)
    nb = nbformat.read(path, as_version=4)
    assert nb.metadata.kernelspec.name == "py5"
    assert "size(640, 360)" in nb.cells[1].source


def test_new_refuses_to_overwrite(tmp_path: Path, capsys) -> None:
    run(capsys, "new", "a", "--dir", str(tmp_path))
    code, _, err = run(capsys, "new", "a", "--dir", str(tmp_path))
    assert code == 2
    assert "already exists" in err
    code, _, _ = run(capsys, "new", "a", "--dir", str(tmp_path), "--kernel", "py5bot", "--force")
    assert code == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["new", "a", "--size", "big"],
        ["new", "a", "--size", "0x10"],
        ["new", "a", "--kernel", "julia"],
        ["run", "x.ipynb", "--timeout", "-1"],
        ["run", "x.ipynb", "--screen", "huge"],
        ["lab", "--port", "70000"],
        ["doctor", "--", "extra"],
    ],
)
def test_bad_arguments_are_usage_errors(argv, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "error" in capsys.readouterr().err


def _dirty_notebook(path: Path) -> None:
    nb = cli.new_notebook("py5bot")
    nb.cells[0].execution_count = 1
    nb.cells[0].outputs = [new_output("stream", name="stdout", text="hi")]
    nbformat.write(nb, path)


def test_clean_check_and_fix(tmp_path: Path, capsys) -> None:
    _dirty_notebook(tmp_path / "a.ipynb")
    code, out, _ = run(capsys, "clean", "--check", str(tmp_path))
    assert code == 1 and "has outputs" in out
    code, out, _ = run(capsys, "clean", str(tmp_path))
    assert code == 0 and "cleaned" in out
    code, out, _ = run(capsys, "clean", "--check", str(tmp_path))
    assert code == 0
    code, out, _ = run(capsys, "clean", str(tmp_path))
    assert "nothing to clean" in out


def test_clean_reports_invalid_json(tmp_path: Path, capsys) -> None:
    (tmp_path / "broken.ipynb").write_text("not json")
    code, _, err = run(capsys, "clean", str(tmp_path))
    assert code == 2
    assert "error" in err


@pytest.fixture
def fake_display(monkeypatch):
    @contextmanager
    def fake(env=None, *, screen=None):
        yield {"DISPLAY": ":42", "PATH": "/bin"}

    monkeypatch.setattr(cli, "ensure_display", fake)


def test_run_reports_results_and_exports_images(tmp_path: Path, capsys, fake_display, monkeypatch):
    nb_dir = tmp_path / "nbs"
    nb_dir.mkdir()
    for name in ("good", "bad"):
        nbformat.write(cli.new_notebook("python3"), nb_dir / f"{name}.ipynb")
    png = base64.b64encode(b"\x89PNG-data").decode()
    seen_display = []

    def fake_execute(path, *, timeout, kernel_name):
        import os

        seen_display.append(os.environ.get("DISPLAY"))
        nb = nbformat.read(path, as_version=4)
        if path.stem == "bad":
            return nb, RunResult(path, False, 0.1, "NameError: name 'x' is not defined")
        nb.cells[0].outputs = [new_output("display_data", data={"image/png": png})]
        return nb, RunResult(path, True, 0.2)

    monkeypatch.setattr(cli, "execute_notebook", fake_execute)
    out_dir, img_dir = tmp_path / "out", tmp_path / "img"
    code, out, err = run(
        capsys, "run", str(nb_dir), "--output-dir", str(out_dir), "--images-dir", str(img_dir)
    )
    assert code == 1
    assert "[FAIL]" in out and "NameError" in out and "1/2 notebook(s) passed" in out
    assert "DISPLAY=:42" in err
    assert seen_display == [":42", ":42"]
    assert sorted(p.name for p in out_dir.iterdir()) == ["bad.ipynb", "good.ipynb"]
    assert [p.name for p in img_dir.iterdir()] == ["good-cell01-01.png"]
    assert (img_dir / "good-cell01-01.png").read_bytes() == b"\x89PNG-data"


def test_run_all_pass(tmp_path: Path, capsys, fake_display, monkeypatch):
    nbformat.write(cli.new_notebook("py5"), tmp_path / "a.ipynb")
    monkeypatch.setattr(
        cli,
        "execute_notebook",
        lambda path, **kw: (nbformat.read(path, as_version=4), RunResult(path, True, 0.0)),
    )
    code, out, _ = run(capsys, "run", str(tmp_path / "a.ipynb"))
    assert code == 0 and "1/1" in out


def test_run_empty_directory(tmp_path: Path, capsys) -> None:
    code, _, err = run(capsys, "run", str(tmp_path))
    assert code == 2 and "no notebooks" in err


def test_run_missing_path(tmp_path: Path, capsys) -> None:
    code, _, err = run(capsys, "run", str(tmp_path / "nope.ipynb"))
    assert code == 2 and "not a notebook" in err


def test_lab_dry_run_uses_env_and_passthrough(capsys, monkeypatch) -> None:
    monkeypatch.setenv("PY5_LAB_IP", "0.0.0.0")
    monkeypatch.setenv("PY5_LAB_PORT", "9001")
    code, out, _ = run(capsys, "lab", "--root-dir", "/nb", "--dry-run", "--", "--debug")
    assert code == 0
    assert "--ServerApp.ip=0.0.0.0" in out
    assert "--ServerApp.port=9001" in out
    assert "--ServerApp.root_dir=/nb" in out
    assert out.strip().endswith("--debug")


def test_lab_cli_args_override_env(capsys, monkeypatch) -> None:
    monkeypatch.setenv("PY5_LAB_PORT", "9001")
    code, out, _ = run(capsys, "lab", "--port", "9100", "--ip", "127.0.0.2", "--dry-run")
    assert code == 0
    assert "--ServerApp.port=9100" in out and "--ServerApp.ip=127.0.0.2" in out


def test_lab_runs_jupyter_with_display(capsys, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        cli, "_run_with_display", lambda cmd, screen=None: calls.append((cmd, screen)) or 0
    )
    code, _, _ = run(capsys, "lab", "--screen", "800x600x24")
    assert code == 0
    ((cmd, screen),) = calls
    assert cmd[1:3] == ["-m", "jupyterlab"] and screen == "800x600x24"


def test_lab_invalid_env_port(capsys, monkeypatch) -> None:
    monkeypatch.setenv("PY5_LAB_PORT", "nope")
    code, _, err = run(capsys, "lab", "--dry-run")
    assert code == 2 and "invalid port" in err


def test_with_display_requires_command(capsys) -> None:
    code, _, err = run(capsys, "with-display")
    assert code == 2 and "no command" in err


def test_with_display_runs_command(capsys, monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", ":3")
    code, _, _ = run(
        capsys,
        "with-display",
        "--",
        sys.executable,
        "-c",
        "import os, sys; sys.exit(7 if os.environ['DISPLAY'] == ':3' else 1)",
    )
    assert code == 7


def test_with_display_reports_missing_xvfb(capsys, monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr("jupyterlab_py5.display.shutil.which", lambda _name: None)
    code, _, err = run(capsys, "with-display", "--", "true")
    assert code == 2 and "Xvfb" in err


def test_healthcheck(capsys, monkeypatch) -> None:
    monkeypatch.setenv("PY5_LAB_PORT", "9002")
    seen = []

    def fake_check(url, timeout):
        seen.append((url, timeout))
        return url.endswith(":9002/api"), "msg"

    monkeypatch.setattr(cli, "check_health", fake_check)
    code, out, _ = run(capsys, "healthcheck", "--timeout", "1")
    assert code == 0 and out.strip() == "msg"
    assert seen == [("http://127.0.0.1:9002/api", 1.0)]
    code, _, _ = run(capsys, "healthcheck", "--url", "http://127.0.0.1:1/other")
    assert code == 1


def test_doctor_exit_code(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "run_checks", lambda: [Check("java", Status.FAIL, "none", "fix")])
    code, out, _ = run(capsys, "doctor")
    assert code == 1 and "[FAIL] java" in out
    monkeypatch.setattr(cli, "run_checks", lambda: [Check("java", Status.OK, "Java 21")])
    code, out, _ = run(capsys, "doctor")
    assert code == 0


def test_module_entry_point() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "jupyterlab_py5", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout
