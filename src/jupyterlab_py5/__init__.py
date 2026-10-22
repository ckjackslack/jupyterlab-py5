"""Helpers for running py5 sketches inside JupyterLab (in Docker or locally)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jupyterlab-py5")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
