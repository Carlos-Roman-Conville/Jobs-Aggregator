"""Unit tests for attached resume parsing gates."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.attached_resume import parse_attached_resume, validate_parsed_resume_text  # noqa: E402


class TestAttachedResume(unittest.TestCase):
    def test_validate_min_chars(self):
        ok, reason = validate_parsed_resume_text("short")
        self.assertFalse(ok)
        self.assertIn("too short", reason)

    def test_parse_txt_creates_sidecar(self):
        long_text = "Experience: " + ("Linux support. " * 80)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "resume.txt"
            src.write_text(long_text, encoding="utf-8")
            text, warn = parse_attached_resume(str(src))
            self.assertIsNone(warn)
            self.assertGreaterEqual(len(text), 400)
            cache = src.with_suffix(src.suffix + ".parsed.txt")
            self.assertTrue(cache.is_file())


if __name__ == "__main__":
    unittest.main()
