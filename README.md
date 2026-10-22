# jupyterlab-py5

Run [py5](https://py5coding.org/) (Processing for Python) sketches in JupyterLab — in Docker or
locally — with a **headless virtual display**, so sketches that normally need a window also run on
a server or in CI.

py5 drives the Java Processing libraries through JPype, so it needs a JDK. This project packages
that stack as a reproducible Docker image and as a small installable helper (`jupyterlab-py5`) that
starts a virtual display when no X server is present, launches JupyterLab, and executes notebooks
headlessly.

## Features

- **Docker image** on Python 3.13 + a Temurin JRE 21, with the `py5` and `py5bot` Jupyter kernels
  and a virtual X display (Xvfb) started automatically — no host networking or `.Xauthority` mounts.
- **`jupyterlab-py5` CLI** for use with or without Docker: start a lab, run notebooks headlessly,
  scaffold a sketch, strip notebook outputs, and check the environment.
- **Headless rendering**: both windowed sketches (`run_sketch()`) and static drawings (`py5bot`)
  render against Xvfb, and every produced image can be exported to PNG — handy for CI smoke tests.
- Three example notebooks covering interactive, static, and module-mode sketches.

## Requirements

- **Docker** (with Compose v2) for the container route — nothing else needed.
- For the **local** route: Python ≥ 3.12, a **JDK/JRE 17+** (21 recommended), and **Xvfb**
  (`apt install xvfb`) if the machine has no X server.

## Install (local)

```bash
pip install -e .            # or: uv sync
# optional: the heavier py5 extras (shapely, trimesh, matplotlib, …)
pip install -e '.[extras]'
```

Check everything is wired up:

```bash
jupyterlab-py5 doctor
```

```
[  ok] python           3.13.16
[  ok] java             Java 21
[  ok] python packages  all importable
[  ok] kernels          py5, py5bot, python3
[  ok] display          no DISPLAY; will start Xvfb (/usr/bin/Xvfb)
```

## Usage

### Docker

```bash
cp .env.example .env            # optional: change port, token, screen size, UID
docker compose up               # JupyterLab at http://127.0.0.1:8888 (token printed on start)
docker compose --profile test run --rm smoke   # execute every notebook headlessly, non-zero on failure
```

By default the port is bound to loopback only; set `JUPYTER_BIND=0.0.0.0` in `.env` to expose it
on the LAN.

### Local CLI

```bash
# Start JupyterLab; Xvfb is started automatically if DISPLAY is unset
jupyterlab-py5 lab

# Execute notebooks top-to-bottom and save every rendered frame (CI smoke test)
jupyterlab-py5 run notebooks/ --images-dir frames/

# Scaffold a new sketch notebook
jupyterlab-py5 new "spiral study" --kernel py5 --size 600x600

# Run any command inside a virtual display (e.g. a plain .py sketch)
jupyterlab-py5 with-display -- python my_sketch.py

# Strip outputs before committing
jupyterlab-py5 clean notebooks/
```

Running `jupyterlab-py5 run notebooks/` here executes all three example notebooks and writes PNGs:

```
[ok  ] notebooks/01-interactive-sketch.ipynb (7.8s, 1 image(s))
[ok  ] notebooks/02-static-drawing-py5bot.ipynb (4.3s, 2 image(s))
[ok  ] notebooks/03-module-mode-render.ipynb (4.2s, 2 image(s))
3/3 notebook(s) passed
```

## Configuration

CLI flags take precedence over environment variables:

| Variable | Used by | Meaning |
|---|---|---|
| `PY5_LAB_IP`, `PY5_LAB_PORT`, `PY5_LAB_ROOT` | `lab` | listen interface, port, notebook dir |
| `JUPYTER_TOKEN` | `lab`, Docker | login token (random if unset) |
| `PY5_SCREEN` | any display command | Xvfb screen as `WxHxDEPTH` (default `1280x1024x24`) |
| `JUPYTER_HOST_PORT`, `JUPYTER_BIND`, `NB_UID` | Docker | host port, bind address, container user id |

## Design

- **`display.py`** — starts/stops an Xvfb process and sets `DISPLAY`; a no-op when a display
  already exists.
- **`java.py`** / **`doctor.py`** — locate a JDK and verify py5, the kernels and the display.
- **`lab.py`** — build and launch the JupyterLab command inside a display.
- **`notebooks.py`** — execute notebooks with nbclient and extract rendered images.
- **`cli.py`** — the argparse front end (`lab`, `run`, `new`, `clean`, `doctor`, `with-display`,
  `healthcheck`).

Side effects (processes, the filesystem, the network) live at the edges; the rest is plain
functions, which is what the test suite exercises.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

Most tests are offline and fake the display/JDK/subprocess boundaries. Tests marked `integration`
start a real Xvfb and execute real notebooks with py5; they skip automatically when a JDK, Xvfb or
py5 is unavailable.

## Modernised from the original

The original was a single `Dockerfile` + `docker-compose.yml` proof of concept. Brought up to date:

- `python:3.10` + a huge apt list + Poetry → slim **Python 3.13** image with a **Temurin JRE 21**
  and **uv**; runtime libraries only.
- Windowed-only, machine-specific display (`DISPLAY=:0`, host `network_mode`, mounted
  `.Xauthority`) → **Xvfb started in-container**, portable Compose, loopback-bound port + token.
- Dead `JAVA_OPTS`/`MaxPermSize` and the invalid `--ip='*'` → removed/fixed.
- Unpinned template `pyproject.toml` → **PEP 621** packaging, pinned deps, ruff, a `hadolint`-clean
  Dockerfile.

## Known limitations

- Only software OpenGL (Mesa) is available in the container, so `P2D`/`P3D` sketches render but
  without GPU acceleration.
- The live JupyterLab UI isn't verified by automated tests (the `run`/`doctor`/image paths are);
  `healthcheck` is provided for liveness.
- py5 and py5jupyter publish only alpha releases, so their versions are pinned to pre-release lower
  bounds on purpose.
