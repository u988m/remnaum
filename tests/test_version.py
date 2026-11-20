from starlette.requests import Request
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.main import app
from app import version
from app.web.routes import admin_dashboard_page
from scripts.release_version import BUILD_VERSION, DEV_VERSION, git_version


class VersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_renders_installed_build_separately_from_github_release(self):
        self.assertEqual(app.version, version.__version__)
        with patch.object(version, "__version__", "1.2.3+g0123456789ab"):
            page = await admin_dashboard_page(Request({"type": "http", "headers": []}))
        html = page.body.decode()
        self.assertIn('<dt>Установленная версия</dt><dd id="remnaum-version">1.2.3+g0123456789ab</dd>', html)
        self.assertIn('<dt>Последний релиз GitHub</dt><dd id="remnaum-github-version"', html)
        self.assertIn('aria-live="polite">Загружаем…</dd>', html)
        self.assertEqual(page.headers["Cache-Control"], "no-cache")
        for name in ("admin.css", "admin.js"):
            self.assertRegex(html, rf'/static/{name}\?v=[0-9a-f]{{16}}"')

    async def test_published_release_is_loaded_without_build_metadata_and_cached(self):
        response = httpx.Response(200, json={"tag_name": "v1.2.3"}, request=httpx.Request("GET", version.RELEASE_URL))
        client = AsyncMock()
        client.__aenter__.return_value.get.return_value = response
        with patch.object(version, "__version__", DEV_VERSION), patch.object(version, "_release", None), \
             patch.object(version, "_release_expires_at", 0), patch.object(version.httpx, "AsyncClient", return_value=client):
            self.assertEqual(await version.latest_release_version(), "1.2.3")
            self.assertEqual(await version.latest_release_version(), "1.2.3")
        client.__aenter__.return_value.get.assert_awaited_once()

    async def test_unavailable_github_uses_only_cached_github_version(self):
        client = AsyncMock()
        client.__aenter__.return_value.get.side_effect = httpx.ConnectError("offline")
        with patch.object(version, "__version__", "1.2.3+g0123456789ab"), patch.object(version, "_release", None), \
             patch.object(version, "_release_expires_at", 0), patch.object(version.httpx, "AsyncClient", return_value=client):
            self.assertIsNone(await version.latest_release_version())
            version._release = "1.2.4"
            version._release_expires_at = 0
            self.assertEqual(await version.latest_release_version(), "1.2.4")
            self.assertEqual(version.installed_version(), "1.2.3+g0123456789ab")

    def test_container_metadata_takes_precedence_over_git(self):
        with patch.dict(os.environ, {BUILD_VERSION: "1.2.3+g0123456789ab"}):
            self.assertEqual(version.read_version(), "1.2.3+g0123456789ab")

    def test_source_without_git_has_a_development_version(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(git_version(Path(directory)), DEV_VERSION)

    @unittest.skipUnless(shutil.which("git"), "git is required to test release tags")
    def test_tagged_checkout_and_commits_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", directory, *args], text=True, stderr=subprocess.DEVNULL,
                ).strip()

            git("init")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test",
                "commit", "--allow-empty", "-m", "Release")
            release_commit = git("rev-parse", "--short=12", "HEAD")
            self.assertEqual(git_version(root), f"0.0.0+g{release_commit}")
            git("tag", "v1.2.3")
            self.assertEqual(git_version(root), "1.2.3")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test",
                "commit", "--allow-empty", "-m", "Follow-up")
            self.assertEqual(git_version(root), "1.2.3+g" + git("rev-parse", "--short=12", "HEAD"))
