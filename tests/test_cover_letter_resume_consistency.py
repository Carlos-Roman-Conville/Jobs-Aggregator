"""Unit tests for resume<->letter consistency check."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.package_build import (  # noqa: E402
    consistency_check_llm,
    extract_resume_bullets_from_content,
)


class TestResumeLetterConsistency(unittest.TestCase):
    def test_extract_bullets_from_tailored_content(self):
        content = {
            "summary": "IT support specialist.",
            "experience": [
                {
                    "title": "Sysadmin",
                    "company": "Acme",
                    "bullets": ["Managed Linux servers.", "Resolved tickets."],
                }
            ],
        }
        bullets = extract_resume_bullets_from_content(content)
        self.assertIn("IT support specialist.", bullets[0])
        self.assertIn("Managed Linux servers.", bullets)

    @patch("job_pipeline.package_build.writing_providers_available", return_value=True)
    @patch("job_pipeline.package_build.generate_json")
    def test_consistency_flags_contradiction(self, mock_generate, _avail):
        mock_generate.return_value = {"warnings": ["Letter claims 10 years AD experience not in resume bullets."]}

        warnings = consistency_check_llm(
            "I have ten years of Active Directory experience.",
            "IT Support",
            "Acme",
            ["Linux", "help desk"],
            resume_bullets=["Supported Linux endpoints."],
            summary_card={"verdict": "maybe", "gaps": ["Active Directory"], "boost_signals": []},
        )
        self.assertTrue(warnings)
        user = mock_generate.call_args.kwargs["user"]
        self.assertIn("RESUME_BULLETS", user)
        self.assertIn("CURATED_SUMMARY_CARD", user)
        self.assertIn("Supported Linux endpoints", user)


if __name__ == "__main__":
    unittest.main()
