import re
import unittest
from pathlib import Path

from app.main import app
from app.version import __version__, read_version


class VersionTests(unittest.TestCase):
    def test_version_is_loaded_from_single_file(self):
        canonical = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(canonical, r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
        self.assertEqual(canonical, read_version())
        self.assertEqual(app.version, canonical)
        self.assertEqual(__version__, canonical)
