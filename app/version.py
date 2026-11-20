"""Build metadata and the current published Remnaum release."""
import asyncio
import re
from time import monotonic

import httpx

from scripts.release_version import read_version

__version__ = read_version()
RELEASE_REPOSITORY = "u988m/remnaum"
RELEASE_URL = f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases/latest"
RELEASE_NUMBER = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"

_release_lock = asyncio.Lock()
_release: str | None = None
_release_expires_at = 0.0


def installed_version() -> str:
    return __version__


async def latest_release_version() -> str | None:
    global _release, _release_expires_at
    if monotonic() >= _release_expires_at:
        async with _release_lock:
            if monotonic() >= _release_expires_at:
                try:
                    async with httpx.AsyncClient(timeout=3) as client:
                        response = await client.get(RELEASE_URL, headers={
                            "Accept": "application/vnd.github+json",
                            "User-Agent": "Remnaum",
                        })
                        response.raise_for_status()
                        payload = response.json()
                    tag = payload.get("tag_name") if isinstance(payload, dict) else None
                    if not isinstance(tag, str) or not re.fullmatch(rf"v?{RELEASE_NUMBER}", tag):
                        raise ValueError("Invalid Remnaum release tag")
                except (httpx.HTTPError, ValueError):
                    _release_expires_at = monotonic() + 60
                else:
                    _release = tag.removeprefix("v")
                    _release_expires_at = monotonic() + 1800
    return _release
