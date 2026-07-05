"""Round 2 quality tests: skills curation, account wording, vague verbs."""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.cover_letter_tailor import _normalize_cover_letter_content  # noqa: E402
from job_pipeline.named_requirements import (  # noqa: E402
    check_account_management_wording,
    curate_technical_skills,
    find_vague_verb_violations,
    user_account_management_level,
)
from job_pipeline.resume_tailor import _downgrade_strong_account_wording  # noqa: E402


PROFILE_FULL_ACCOUNT = """
Office 365 admin console — added/removed users and password resets for ~50 staff.
"""

PROFILE_PARTIAL_ACCOUNT = """
Help desk onboarding documentation and end-user support for new hires.
"""


class TestSkillsCuration(unittest.TestCase):
    def test_drops_study_tools_not_in_jd(self):
        content = {
            "skills": {
                "technical": [
                    "Microsoft 365",
                    "Freshdesk",
                    "Wireshark (study)",
                    "Linux",
                    "Active Directory",
                ]
                + [f"Skill{i}" for i in range(30)],
                "soft": [],
            }
        }
        jd = "Microsoft 365 and Freshdesk help desk support required."
        curated, notes = curate_technical_skills(content, jd, PROFILE_PARTIAL_ACCOUNT)
        self.assertLessEqual(len(curated), 22)
        self.assertNotIn("Wireshark (study)", curated)
        self.assertTrue(any("curated technical skills" in n for n in notes))

    def test_keeps_study_tool_when_jd_asks(self):
        content = {
            "skills": {
                "technical": ["Wireshark (study)", "Linux"] + [f"Extra{i}" for i in range(25)],
                "soft": [],
            }
        }
        jd = "Wireshark packet analysis and Linux admin."
        curated, notes = curate_technical_skills(content, jd, "")
        self.assertIn("Wireshark (study)", curated)


class TestAccountManagementWording(unittest.TestCase):
    def test_level_full_vs_partial(self):
        self.assertEqual(user_account_management_level(PROFILE_FULL_ACCOUNT), "full")
        self.assertEqual(user_account_management_level(PROFILE_PARTIAL_ACCOUNT), "partial")

    def test_flags_strong_wording_on_partial_profile(self):
        content = {
            "summary": "Proven ability to manage user accounts across the org.",
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        issues = check_account_management_wording(content, PROFILE_PARTIAL_ACCOUNT)
        self.assertTrue(issues)

    def test_downgrades_inflated_phrasing(self):
        content = {
            "summary": "IT Support with proven ability to manage user accounts.",
            "experience": [
                {
                    "title": "Tech",
                    "company": "Co",
                    "duration": "2y",
                    "bullets": ["Managed user accounts daily."],
                }
            ],
            "skills": {"technical": ["User account management"], "soft": []},
        }
        issues = []
        _downgrade_strong_account_wording(content, PROFILE_PARTIAL_ACCOUNT, issues)
        self.assertIn("user account support", content["summary"].lower())
        self.assertTrue(issues)


class TestVagueVerbs(unittest.TestCase):
    def test_detects_leveraged(self):
        self.assertIn("leveraged", find_vague_verb_violations("I leveraged Microsoft 365."))

    def test_normalize_rejects_vague_verbs(self):
        with self.assertRaises(ValueError):
            _normalize_cover_letter_content(
                {
                    "opening": "Hello.",
                    "body_paragraphs": ["I leveraged Microsoft 365 in past roles."],
                    "closing": "Thanks.",
                }
            )


if __name__ == "__main__":
    unittest.main()
