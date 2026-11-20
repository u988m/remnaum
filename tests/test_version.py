import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import app
from app.version import __version__, read_version
from app.web.routes import admin_dashboard_page
from scripts.release_version import BUILD_VERSION, DEV_VERSION, git_version


class VersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_and_api_show_the_running_version(self):
        self.assertEqual(app.version, __version__)
        page = await admin_dashboard_page()
        self.assertIn(f'id="remnaum-version">Remnaum {__version__}', page.body.decode())

    def test_container_metadata_takes_precedence_over_git(self):
        with patch.dict(os.environ, {BUILD_VERSION: "1.2.3+g0123456789ab"}):
            self.assertEqual(read_version(), "1.2.3+g0123456789ab")

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
