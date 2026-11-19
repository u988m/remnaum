"""Remnaum release version — single source of truth: repository-root VERSION file.

For the next release edit VERSION only; the API and tests read it at runtime.
"""
from pathlib import Path
import re


def read_version() -> str:
    value = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"0|[1-9]\d*(?:\.(?:0|[1-9]\d*)){2}(?:[-+][0-9A-Za-z.-]+)?", value):
        raise ValueError("VERSION must be a valid X.Y.Z version")
    return value


__version__ = read_version()
