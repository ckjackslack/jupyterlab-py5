# jupyterlab-py5

> **DRAFT — assessment only. TODO / to be finished.**

## What this seems to be

A Docker / docker-compose setup for running [py5](https://py5coding.org/) (Processing for
CPython, which drives the Java Processing jars through JPype and therefore needs a JDK) inside
JupyterLab, with the py5 Jupyter kernels installed and one example notebook
(`base/code/Py5Example.ipynb`) that draws squares following the mouse and changes the fill
colour on click.

## Current state (as found on `master`, April 2024)

What is there:

- `base/Dockerfile` — `python:3.10` image, a very long apt list, poetry, `pip install -r
  requirements.txt`, installs the `py5` and `py5bot` kernels, runs `jupyter lab`.
- `docker-compose.yml` — builds `./base`, mounts `./base/code`, mounts the host's
  `$HOME/.Xauthority`, `network_mode: host`.
- `run.sh` — `docker-compose up`.

What is broken or questionable (not run yet, read from the source):

- `CMD [..., "--ip='*'", ...]` is exec form, so the single quotes reach Jupyter literally
  (`'*'` is not a valid IP); it should be `--ip=0.0.0.0`.
- `JAVA_OPTS` sets `-XX:MaxPermSize`, removed in Java 8, and `java.awt.headless=true`, which
  contradicts `DISPLAY=:0`. JPype doesn't read `JAVA_OPTS` anyway, so the whole line is dead.
- `DISPLAY=:0` with no X server started: `xvfb` is installed but never launched, so a
  windowed sketch (`run_sketch()`) only works if the host's X server is reachable through
  `network_mode: host` + the mounted `.Xauthority` — a Linux-desktop-only, machine-specific
  assumption.
- Poetry is installed from Debian's `python3-poetry` (built for the system Python, not the
  image's `/usr/local` Python) and is used only as `poetry run pip install`; `pyproject.toml`
  is an unfilled template (`your-project-name`, `you@example.com`).
- `requirements.txt` is unpinned and pulls many unrelated libraries (`pygame`, `arcade`, `p5`,
  `scikit-learn`, ...); the image also installs `texlive`, `gdb`, `nodejs`, autotools etc.
  → very large, slow image.
- Notebook lives in `base/code` but is mounted to `/opt/app/src` while Jupyter starts in
  `/opt/app`; `version: '3.8'` in compose is obsolete.
- No secrets. Personal/machine-specific bits: the `$HOME/.Xauthority` mount and host networking.
- No tests, no README.

## TODO / to be finished

- Rebuild the image around a current Python + JDK, start Xvfb, show sketches in the notebook.
- Make compose portable (no host network / Xauthority), configurable port/token.
- Verify outside Docker by executing the notebook headlessly; add pytest.
