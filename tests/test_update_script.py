"""Verify the updater against temporary Git repos and a simulated Docker CLI."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "update.sh"


@unittest.skipUnless(shutil.which("flock") and shutil.which("git"), "requires git and flock")
class UpdateScriptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.origin = root / "source"
        self.install = root / "install"
        self.origin.mkdir()
        self.install.mkdir()
        self.env = os.environ.copy()
        self.env.pop("SUDO_USER", None)
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        })
        self.git(self.origin, "init", "-b", "main")
        shutil.copyfile(SOURCE, self.origin / "update.sh")
        (self.origin / "backup.sh").write_text("touch backup-created\n")
        (self.origin / "version").write_text("previous")
        self.git(self.origin, "add", ".")
        self.git(self.origin, "commit", "-m", "Initial")
        self.previous = self.git(self.origin, "rev-parse", "HEAD")
        self.git(root, "clone", str(self.origin), str(self.install))
        (self.install / ".env").write_text("COMPOSE_FILE=compose.yaml\n")
        (self.origin / "version").write_text("next")
        self.git(self.origin, "commit", "-am", "Update")
        self.git(self.origin, "tag", "v1.0.0")
        self.target = self.git(self.origin, "rev-parse", "HEAD")
        (self.origin / "version").write_text("main")
        self.git(self.origin, "commit", "-am", "Another update")
        docker = root / "bin" / "docker"
        docker.parent.mkdir()
        docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> docker.log
if [ "${FAIL_NEW_STACK:-}" = 1 ] && [ "$2" = up ] && [ "$(cat version)" = next ]; then exit 1; fi
""")
        docker.chmod(0o755)

    def git(self, cwd, *args):
        return subprocess.check_output(["git", *args], cwd=cwd, env=self.env, text=True,
                                       stderr=subprocess.PIPE).strip()

    def update(self, *args, **env):
        return subprocess.run(["bash", "update.sh", *args], cwd=self.install, env=self.env | env,
                              text=True, capture_output=True)

    def test_update_uses_exact_tag_and_runs_health_check(self):
        result = self.update("v1.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.install, "rev-parse", "HEAD"), self.target)
        self.assertTrue((self.install / "backup-created").exists())
        self.assertIn("compose up -d --wait", (self.install / "docker.log").read_text())

    def test_failed_health_rolls_back_without_restoring_data(self):
        result = self.update("v1.0.0", FAIL_NEW_STACK="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.install, "rev-parse", "HEAD"), self.previous)
        self.assertTrue((self.install / "backup-created").exists())
        self.assertIn("compose up -d --build --wait", (self.install / "docker.log").read_text())

    def test_update_keeps_existing_local_changes(self):
        (self.install / "version").write_text("local")
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.install / "version").read_text(), "local")
        self.assertFalse((self.install / "backup-created").exists())
