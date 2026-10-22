# syntax=docker/dockerfile:1
#
# JupyterLab + py5 (Processing for Python) with a JRE and a virtual X display.
# Build:  docker compose build      Run:  docker compose up

ARG PYTHON_VERSION=3.13
ARG JAVA_VERSION=21
ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM eclipse-temurin:${JAVA_VERSION}-jre AS jre

FROM python:${PYTHON_VERSION}-slim-trixie

ARG NB_USER=py5
ARG NB_UID=1000

# Runtime libraries only: Xvfb for the virtual display, the X/AWT libraries the
# JRE links against, Mesa (software OpenGL) for the P2D/P3D renderers, fonts for
# text(), and tini as PID 1.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb \
      libxext6 libxrender1 libxtst6 libxi6 \
      libfreetype6 fontconfig fonts-dejavu-core \
      libgl1 libegl1 libgl1-mesa-dri \
      tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=jre /opt/java/openjdk /opt/java/openjdk
COPY --from=uv /uv /usr/local/bin/uv

ENV JAVA_HOME=/opt/java/openjdk \
    VIRTUAL_ENV=/opt/venv \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:${JAVA_HOME}/bin:${PATH}"

WORKDIR /opt/jupyterlab-py5

# Dependencies first (cached layer), then the small helper package itself.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra extras
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --extra extras \
 && python -m py5jupyter.kernels.py5.install --sys-prefix \
 && python -m py5jupyter.kernels.py5bot.install --sys-prefix

RUN useradd --create-home --uid "${NB_UID}" --shell /bin/bash "${NB_USER}"
USER ${NB_USER}
WORKDIR /home/${NB_USER}/notebooks
# Baked-in examples; compose mounts ./notebooks over this directory.
COPY --chown=${NB_UID} notebooks/ ./

ENV PY5_LAB_IP=0.0.0.0 \
    PY5_LAB_PORT=8888 \
    PY5_SCREEN=1280x1024x24
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["jupyterlab-py5", "healthcheck"]

ENTRYPOINT ["tini", "--"]
CMD ["jupyterlab-py5", "lab"]
