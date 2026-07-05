"""Unit tests for cover_letter_tailor (mocked Gemini)."""
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

from job_pipeline.cover_letter_tailor import (  # noqa: E402
    curate_summary_card_for_cover_letter,
    generate_cover_letter_content,
    tailor_cover_letter_from_jd,
)


HONEST_LIMITS_PROFILE = """
# === CAREER MASTER ===
## Honest limits
### No exposure
- Active Directory administration
- Microsoft Intune fleet management

Do NOT claim enterprise cloud architect experience.
"""


class TestCurateSummaryCard(unittest.TestCase):
    def test_curates_fields_only(self):
        raw = {
            "verdict": "strong_match",
            "why_match": "Linux help desk overlap.",
            "gaps": ["AD"],
            "application_friction": "low — easy apply",
            "boost_signals": ["remote_1.25x", "tier1_title_family_1.10x", "extra"],
            "fit_score": 0.99,
            "noise": "should not appear",
        }
        out = curate_summary_card_for_cover_letter(raw)
        self.assertEqual(out["verdict"], "strong_match")
        self.assertEqual(out["why_match"], "Linux help desk overlap.")
        self.assertEqual(out["gaps"], ["AD"])
        self.assertEqual(len(out["boost_signals"]), 3)
        self.assertNotIn("fit_score", out)
        self.assertNotIn("noise", out)


class TestGenerateCoverLetterContent(unittest.TestCase):
    def _mock_resp(self, payload: dict):
        resp = MagicMock()
        resp.text = json.dumps(payload)
        return resp

    @patch("job_pipeline.cover_letter_tailor.writing_providers_available", return_value=True)
    @patch("job_pipeline.cover_letter_tailor.generate_json")
    def test_honest_limits_in_prompt(self, mock_generate, _avail):
        mock_generate.return_value = {
            "opening": "I am interested in the role.",
            "body_paragraphs": ["I support Linux servers and help desk tickets."],
            "closing": "Thank you for your consideration.",
        }
        generate_cover_letter_content(
            job_title="IT Support",
            company="Acme",
            location="Remote",
            job_description="Need Active Directory and Linux support.",
            profile_text=HONEST_LIMITS_PROFILE,
            summary_card={"verdict": "maybe", "gaps": ["Active Directory"]},
        )
        user = mock_generate.call_args.kwargs["user"]
        self.assertIn("HONEST LIMITS RULE", user)
        self.assertIn("Active Directory administration", user)
        self.assertNotIn('"fit_score"', user)

    @patch("job_pipeline.cover_letter_tailor.writing_providers_available", return_value=True)
    @patch("job_pipeline.cover_letter_tailor.generate_json")
    def test_curated_summary_card_parameterized(self, mock_generate, _avail):
        mock_generate.return_value = {
            "opening": "Hook.",
            "body_paragraphs": ["Body."],
            "closing": "Close.",
        }
        card = {
            "verdict": "strong_match",
            "why_match": "Help desk + Linux.",
            "gaps": ["SCCM"],
            "application_friction": "medium — long form",
            "boost_signals": ["remote_1.25x"],
        }
        generate_cover_letter_content(
            job_title="Help Desk",
            company="Beta Co",
            location="Philadelphia, PA",
            job_description="Windows and Linux support.",
            profile_text="Profile with Linux experience.",
            summary_card=card,
            resume_text="Beat the Bomb — Linux admin.",
        )
        user = mock_generate.call_args.kwargs["user"]
        self.assertIn('"verdict": "strong_match"', user)
        self.assertIn("remote_1.25x", user)
        self.assertIn("Beat the Bomb", user)

    @patch("job_pipeline.cover_letter_tailor.writing_providers_available", return_value=True)
    @patch("job_pipeline.cover_letter_tailor.generate_json")
    def test_leveraged_microsoft_365_fixed_in_one_call(self, mock_generate, _avail):
        mock_generate.return_value = {
            "opening": "I am interested in the role.",
            "body_paragraphs": ["I leveraged Microsoft 365 for users."],
            "closing": "Thank you for your consideration.",
        }
        out = generate_cover_letter_content(
            job_title="Help Desk",
            company="Acme",
            location="Remote",
            job_description="Microsoft 365 help desk support.",
            profile_text=HONEST_LIMITS_PROFILE,
        )
        self.assertNotIn("error", out)
        body = " ".join(out.get("body_paragraphs") or [])
        self.assertIn("used Microsoft 365", body)
        self.assertNotIn("leveraged", body.lower())
        self.assertEqual(mock_generate.call_count, 1)

    @patch("job_pipeline.cover_letter_tailor.writing_providers_available", return_value=True)
    @patch("job_pipeline.cover_letter_tailor.generate_json")
    def test_jd_years_echo_fixed_not_errored(self, mock_generate, _avail):
        profile = """
IT Support Specialist with 3+ years of help desk experience.
Microsoft 365 and Google Workspace support for end users.
"""
        jd = """
Help Desk Technician — 3-5 years of help desk experience required.
Microsoft 365 and ticketing support.
"""
        mock_generate.return_value = {
            "opening": "I am interested in this help desk role.",
            "body_paragraphs": ["I have 3-5 years of help desk experience."],
            "closing": "Thank you for your consideration.",
        }
        out = generate_cover_letter_content(
            job_title="Help Desk Technician",
            company="Acme",
            location="Remote",
            job_description=jd,
            profile_text=profile,
        )
        self.assertNotIn("error", out)
        body = " ".join(out.get("body_paragraphs") or [])
        self.assertNotIn("3-5 years", body.lower())
        self.assertIn("3+ years", body.lower())
        self.assertEqual(mock_generate.call_count, 1)

    @patch("job_pipeline.cover_letter_tailor.writing_providers_available", return_value=True)
    @patch("job_pipeline.cover_letter_tailor.generate_json")
    def test_empty_content_returns_error(self, mock_generate, _avail):
        mock_generate.return_value = {
            "opening": "",
            "body_paragraphs": [],
            "closing": "",
        }
        out = generate_cover_letter_content(
            job_title="Help Desk",
            company="Acme",
            location="Remote",
            job_description="Need Linux support.",
            profile_text=HONEST_LIMITS_PROFILE,
        )
        self.assertEqual(out.get("error"), "json_parse_failed")
        self.assertEqual(mock_generate.call_count, 2)

    @patch("job_pipeline.cover_letter_tailor.writing_providers_available", return_value=True)
    @patch("job_pipeline.cover_letter_tailor.generate_json")
    def test_happy_path_one_call(self, mock_generate, _avail):
        mock_generate.return_value = {
            "opening": "I am interested in the IT Support role at Acme.",
            "body_paragraphs": [
                "I support Windows endpoints and help desk tickets with clear documentation."
            ],
            "closing": "Thank you for your consideration.",
        }
        out = generate_cover_letter_content(
            job_title="IT Support",
            company="Acme",
            location="Remote",
            job_description="Windows and Linux support.",
            profile_text=HONEST_LIMITS_PROFILE,
        )
        self.assertNotIn("error", out)
        self.assertEqual(out.get("_cl_warnings"), [])
        self.assertEqual(mock_generate.call_count, 1)

    @patch("job_pipeline.cover_letter_tailor._load_grounded_profile_text", return_value=HONEST_LIMITS_PROFILE)
    @patch("job_pipeline.cover_letter_tailor.generate_cover_letter_content")
    def test_tailor_from_jd_ok(self, mock_gen, _prof):
        mock_gen.return_value = {
            "opening": "Hi.",
            "body_paragraphs": ["Details."],
            "closing": "Bye.",
        }
        out = tailor_cover_letter_from_jd("Need Linux support.", job_title="Tech", company="Co")
        self.assertTrue(out["ok"])
        self.assertEqual(out["content"]["opening"], "Hi.")


if __name__ == "__main__":
    unittest.main()
