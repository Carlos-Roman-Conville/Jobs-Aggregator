"""Unit tests for package_build metadata warnings."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.package_build import build_package_metadata  # noqa: E402


class TestBuildPackageMetadata(unittest.TestCase):
    def _letter(self) -> str:
        return "Dear Hiring Team,\n\n" + ("Body paragraph. " * 30) + "\n\nThank you."

    def test_warns_on_unknown_resume_id_without_tailored_artifact(self):
        meta = build_package_metadata(
            "resume_1",
            "template_main",
            self._letter(),
            "Help Desk",
            "Acme",
            mode="both",
            artifacts={},
            skip_llm_check=True,
        )
        warnings = meta.get("warnings") or []
        self.assertTrue(any("Resume file issue" in w for w in warnings))

    def test_skips_resume_id_warning_when_tailored_pdf_present(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"x" * 100)
            tmp = f.name
        try:
            meta = build_package_metadata(
                "resume_1",
                "template_main",
                self._letter(),
                "Help Desk",
                "Acme",
                mode="both",
                artifacts={"resume_pdf": tmp, "warnings": []},
                skip_llm_check=True,
            )
            warnings = meta.get("warnings") or []
            self.assertFalse(any("Resume file issue" in w for w in warnings))
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
