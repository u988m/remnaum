"""Resolve the installed revision against a reachable release tag."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?"
BUILD_VERSION = "REMNAUM_BUILD_VERSION"
DEV_VERSION = "0.0.0+dev"


def git_version(root: Path = ROOT) -> str:
    if not (root / ".git").exists():
        return DEV_VERSION
    try:
        description = subprocess.run(
            ["git", "-c", f"safe.directory={root.resolve()}", "-C", str(root),
             "describe", "--tags", "--match", "v[0-9]*.[0-9]*.[0-9]*", "--long", "--always", "--abbrev=12"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return DEV_VERSION
    release = re.fullmatch(rf"v({SEMVER})-(\d+)-g([0-9a-f]{{12,64}})", description)
    if release:
        version, distance, commit = release.groups()
        return version if distance == "0" else f"{version}+g{commit}"
    if re.fullmatch(r"[0-9a-f]{12,64}", description):
        return f"0.0.0+g{description}"
    return DEV_VERSION


def read_version() -> str:
    version = os.environ.get(BUILD_VERSION, "")
    if re.fullmatch(rf"{SEMVER}(?:\+[0-9A-Za-z.-]+)?", version):
        return version
    return git_version()


if __name__ == "__main__":
    print(git_version())
