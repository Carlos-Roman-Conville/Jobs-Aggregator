"""Unit tests for svc_build_package mode matrix (mocked builders)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline import service  # noqa: E402


ROW = {
    "status": "approved",
    "title": "IT Support",
    "company_name": "Acme",
    "location": "Remote",
    "description_text": "Linux and help desk duties " * 20,
    "summary_json": {"verdict": "maybe", "why_match": "overlap"},
    "recommended_resume_id": "resume_main",
    "cover_letter_template_id": "template_main",
    "apply_url": "https://example.com/apply",
}


class TestSvcBuildPackageModes(unittest.TestCase):
    @patch("job_pipeline.service.set_item_package", return_value=True)
    @patch("job_pipeline.service.build_package_metadata")
    @patch("job_pipeline.service.build_application_artifacts")
    @patch("job_pipeline.service.get_item")
    def test_both_mode_persists_artifacts(self, mock_get, mock_build, mock_meta, _save):
        mock_get.return_value = dict(ROW)
        mock_build.return_value = {
            "ok": True,
            "mode": "both",
            "letter": "Dear Hiring Team,\n\nBody\n\nSincerely,\nTest",
            "artifacts": {
                "resume_pdf": "/tmp/resume.pdf",
                "cover_pdf": "/tmp/cover.pdf",
                "warnings": [],
            },
            "resume_bullets": ["Linux support"],
        }
        mock_meta.return_value = {
            "mode": "both",
            "resume_pdf": "/tmp/resume.pdf",
            "cover_pdf": "/tmp/cover.pdf",
            "warnings": [],
        }
        out = service.svc_build_package(1, mode="both", tailor_resume=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "both")
        self.assertEqual(out["resume_pdf"], "/tmp/resume.pdf")
        mock_build.assert_called_once()
        self.assertEqual(mock_build.call_args.kwargs["mode"], "both")

    @patch("job_pipeline.service.set_item_package", return_value=True)
    @patch("job_pipeline.service.build_package_metadata")
    @patch("job_pipeline.service.build_application_artifacts")
    @patch("job_pipeline.service.get_item")
    def test_resume_only_skips_cover_paths(self, mock_get, mock_build, mock_meta, _save):
        mock_get.return_value = dict(ROW)
        mock_build.return_value = {
            "ok": True,
            "mode": "resume_only",
            "letter": "",
            "artifacts": {"resume_pdf": "/tmp/resume.pdf", "warnings": []},
            "resume_bullets": [],
        }
        mock_meta.return_value = {"mode": "resume_only", "resume_pdf": "/tmp/resume.pdf", "warnings": []}
        out = service.svc_build_package(2, mode="resume_only")
        self.assertTrue(out["ok"])
        self.assertEqual(out.get("cover_pdf"), None)

    @patch("job_pipeline.service.set_item_package", return_value=True)
    @patch("job_pipeline.service.build_package_metadata")
    @patch("job_pipeline.service.build_application_artifacts")
    @patch("job_pipeline.service.get_item")
    def test_rebuild_skips_gap_llm_and_package_check(self, mock_get, mock_build, mock_meta, _save):
        mock_get.return_value = dict(ROW)
        mock_build.return_value = {
            "ok": True,
            "mode": "both",
            "letter": "Dear Hiring Team,\n\nBody\n\nSincerely,\nTest",
            "artifacts": {"resume_pdf": "/tmp/resume.pdf", "cover_pdf": "/tmp/cover.pdf", "warnings": []},
            "resume_bullets": [],
        }
        mock_meta.return_value = {
            "mode": "both",
            "resume_pdf": "/tmp/resume.pdf",
            "cover_pdf": "/tmp/cover.pdf",
            "warnings": [],
        }
        out = service.svc_build_package(3, mode="both", is_rebuild=True)
        self.assertTrue(out["ok"])
        self.assertFalse(mock_build.call_args.kwargs["gap_use_llm"])
        self.assertTrue(mock_meta.call_args.kwargs["skip_llm_check"])

    def test_render_resume_pdf_via_cli_removed(self):
        import job_pipeline.rendercv_export as rce

        self.assertFalse(hasattr(rce, "render_resume_pdf_via_cli"))


if __name__ == "__main__":
    unittest.main()
