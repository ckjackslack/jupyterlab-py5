"""Static checks of the Docker/compose setup and the example notebooks.

There is no Docker daemon in the development environment, so the image is
validated statically: hadolint (if installed), ``docker compose config`` (needs
only the CLI, not the daemon) and structural assertions.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import nbformat
import pytest
import yaml

from jupyterlab_py5.notebooks import KERNELS, discover_notebooks, has_outputs

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text())
X11 = yaml.safe_load((ROOT / "compose.x11.yaml").read_text())
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
NOTEBOOKS = discover_notebooks([ROOT / "notebooks"])


def dockerfile_instructions() -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) with line continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", DOCKERFILE)
    result = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        instruction, _, args = stripped.partition(" ")
        result.append((instruction.upper(), args.strip()))
    return result


INSTRUCTIONS = dockerfile_instructions()


def args_of(name: str) -> list[str]:
    return [args for instr, args in INSTRUCTIONS if instr == name]


# --- Dockerfile -----------------------------------------------------------------------------


def test_base_images_are_versioned() -> None:
    froms = args_of("FROM")
    assert len(froms) == 3
    for image in froms:
        ref = image.split()[0]
        assert ":" in ref and not ref.endswith(":latest"), ref
    final = froms[-1]
    assert final.startswith("python:${PYTHON_VERSION}-slim")


def test_build_args_have_sane_defaults() -> None:
    args = dict(a.split("=", 1) for a in args_of("ARG") if "=" in a)
    assert int(args["JAVA_VERSION"]) >= 17  # py5 needs Java 17+
    major, minor = (int(x) for x in args["PYTHON_VERSION"].split("."))
    assert (major, minor) >= (3, 12)
    assert re.fullmatch(r"\d+\.\d+\.\d+", args["UV_VERSION"])


def test_runtime_packages_are_lean_and_sufficient() -> None:
    run_lines = " ".join(args_of("RUN"))
    for needed in ("xvfb", "libxtst6", "libxrender1", "libgl1", "fontconfig", "tini"):
        assert needed in run_lines
    for dropped in ("texlive", "gdb", "nodejs", "python3-poetry", "build-essential"):
        assert dropped not in run_lines
    assert "--no-install-recommends" in run_lines
    assert "rm -rf /var/lib/apt/lists/*" in run_lines


def test_dependencies_come_from_the_lock_file() -> None:
    run_lines = " ".join(args_of("RUN"))
    assert "uv sync --frozen" in run_lines
    assert "pip install -r" not in run_lines
    assert "py5jupyter.kernels.py5.install --sys-prefix" in run_lines
    assert "py5jupyter.kernels.py5bot.install --sys-prefix" in run_lines


def test_no_dead_jvm_options_or_quoted_ip() -> None:
    assert "MaxPermSize" not in DOCKERFILE
    assert "JAVA_OPTS" not in DOCKERFILE
    assert "--ip='*'" not in DOCKERFILE
    assert "JAVA_HOME=/opt/java/openjdk" in DOCKERFILE


def test_runs_as_non_root_with_init_and_healthcheck() -> None:
    users = args_of("USER")
    assert users and users[-1] not in {"root", "0"}
    assert json.loads(args_of("ENTRYPOINT")[-1]) == ["tini", "--"]
    assert json.loads(args_of("CMD")[-1]) == ["jupyterlab-py5", "lab"]
    healthcheck = args_of("HEALTHCHECK")[-1]
    assert '"jupyterlab-py5", "healthcheck"' in healthcheck
    assert "PY5_LAB_IP=0.0.0.0" in DOCKERFILE  # reachable through the published port
    assert args_of("EXPOSE") == ["8888"]


def test_copied_sources_exist() -> None:
    for args in args_of("COPY"):
        if "--from=" in args:
            continue
        parts = [p for p in args.split() if not p.startswith("--")]
        for src in parts[:-1]:
            assert (ROOT / src).exists(), f"COPY source missing: {src}"


def test_dockerignore_keeps_context_small() -> None:
    ignored = (ROOT / ".dockerignore").read_text().split()
    for entry in (".git", ".venv", ".env"):
        assert entry in ignored


def _hadolint() -> str | None:
    # hadolint-bin installs next to the interpreter, which may not be on PATH.
    beside = Path(sys.executable).parent / "hadolint"
    return str(beside) if beside.exists() else shutil.which("hadolint")


@pytest.mark.skipif(_hadolint() is None, reason="hadolint not installed")
def test_hadolint_passes() -> None:
    result = subprocess.run(
        [str(_hadolint()), str(ROOT / "Dockerfile")], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- compose --------------------------------------------------------------------------------


def test_compose_service_is_portable() -> None:
    svc = COMPOSE["services"]["jupyterlab"]
    assert "version" not in COMPOSE  # obsolete key
    assert "network_mode" not in svc
    assert not any("Xauthority" in v for v in svc["volumes"])
    assert svc["volumes"] == ["./notebooks:/home/py5/notebooks"]
    (port,) = svc["ports"]
    assert port.startswith("${JUPYTER_BIND:-127.0.0.1}:")
    assert port.endswith(":8888")
    assert svc["build"]["context"] == "."


def test_compose_smoke_profile_runs_notebooks() -> None:
    smoke = COMPOSE["services"]["smoke"]
    assert smoke["profiles"] == ["test"]
    assert smoke["command"][:2] == ["jupyterlab-py5", "run"]
    assert all(v.endswith(":ro") for v in smoke["volumes"])


def test_x11_overlay_only_adds_display() -> None:
    svc = X11["services"]["jupyterlab"]
    assert set(svc) == {"environment", "volumes"}
    assert svc["volumes"] == ["/tmp/.X11-unix:/tmp/.X11-unix:ro"]


def test_env_example_covers_compose_variables() -> None:
    text = (ROOT / "compose.yaml").read_text()
    used = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", text))
    documented = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert used <= documented


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, check=False
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _docker_compose_available(), reason="docker compose CLI not available")
@pytest.mark.parametrize(
    "files", [["compose.yaml"], ["compose.yaml", "compose.x11.yaml"]], ids=["base", "x11"]
)
def test_docker_compose_config_validates(files: list[str], tmp_path: Path) -> None:
    # `docker compose config` parses and interpolates without talking to a daemon.
    cmd = ["docker", "compose", "--profile", "test"]
    for f in files:
        cmd += ["-f", str(ROOT / f)]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path), "DISPLAY": ":1"}
    result = subprocess.run(
        [*cmd, "config", "--format", "json"], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    published = config["services"]["jupyterlab"]["ports"][0]
    assert published["host_ip"] == "127.0.0.1"
    assert str(published["published"]) == "8888"


# --- notebooks ------------------------------------------------------------------------------


def test_example_notebooks_exist() -> None:
    assert [p.name for p in NOTEBOOKS] == [
        "01-interactive-sketch.ipynb",
        "02-static-drawing-py5bot.ipynb",
        "03-module-mode-render.ipynb",
    ]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_notebook_is_valid_clean_and_uses_known_kernel(path: Path) -> None:
    nb = nbformat.read(path, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata.kernelspec.name in KERNELS
    assert not has_outputs(nb), "commit notebooks without outputs (jupyterlab-py5 clean)"
    for cell in nb.cells:
        if cell.cell_type == "code":
            compile(cell.source, str(path), "exec")


# --- packaging ------------------------------------------------------------------------------


def test_pyproject_basics() -> None:
    project = PYPROJECT["project"]
    assert project["requires-python"] == ">=3.12"
    assert project["scripts"]["jupyterlab-py5"] == "jupyterlab_py5.cli:main"
    deps = " ".join(project["dependencies"])
    for gone in ("pygame", "arcade", "p5", "scikit-learn", "poetry"):
        assert not re.search(rf"(^|\s|\"){gone}\b", deps), gone


def test_lock_file_matches_py5_lower_bound() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = {pkg["name"]: pkg["version"] for pkg in lock["package"]}
    bound = re.search(r"py5\[jupyter\]>=([\w.]+)", " ".join(PYPROJECT["project"]["dependencies"]))
    assert bound is not None
    assert versions["py5"] == bound.group(1)
    assert "jupyterlab" in versions and "nbclient" in versions
