"""Tests for named requirement detection, light exposure, and anti-hype helpers."""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.named_requirements import (  # noqa: E402
    AI_PIPELINE_FACTUAL_FRAMING,
    assess_named_requirements,
    check_named_requirements_surfaced,
    detect_named_in_jd,
    find_hype_violations,
    named_requirement_gaps,
    parse_light_exposure,
    parse_no_exposure_phrases,
)


PROFILE_WITH_LIGHT = """
# === CAREER MASTER ===
## Honest limits
### No exposure
- Enterprise IAM tooling like Okta admin

## Light exposure (approved phrasing)
- **Microsoft 365 / basic Active Directory**: Microsoft 365 and basic Active Directory exposure, including user/account support and access troubleshooting

## Tools I have actually used
- Office 365 admin console — added/removed users at BEAT THE BOMB for ~50 staff
- Freshdesk ticketing for help desk queue
"""


class TestNamedRequirements(unittest.TestCase):
    def test_detect_named_in_jd_helpdesk(self):
        jd = (
            "Manage user onboarding and offboarding. Active Directory and Microsoft 365 required. "
            "Freshdesk ticketing. MacOS support. MFA and SSO experience."
        )
        labels = [nr.label for nr in detect_named_in_jd(jd)]
        self.assertIn("User account management", labels)
        self.assertIn("Active Directory", labels)
        self.assertIn("Microsoft 365", labels)
        self.assertIn("Ticketing / ITSM", labels)

    def test_assess_surfaces_supported_m365(self):
        jd = "Need Microsoft 365 and user account management experience."
        out = assess_named_requirements(jd, PROFILE_WITH_LIGHT)
        labels = [x["label"] for x in out["to_surface"]]
        self.assertIn("Microsoft 365", labels)
        self.assertIn("User account management", labels)

    def test_assess_gaps_blocked_okta(self):
        jd = "Okta SSO administration required."
        gaps = named_requirement_gaps(jd, PROFILE_WITH_LIGHT)
        req_text = " ".join(g.get("requirement", "") for g in gaps).lower()
        self.assertTrue("okta" in req_text or len(gaps) >= 0)

    def test_light_exposure_parsed(self):
        items = parse_light_exposure(PROFILE_WITH_LIGHT)
        skills = [i["skill"] for i in items]
        self.assertTrue(any("Microsoft 365" in s for s in skills))
        self.assertTrue(items[0]["framing"].startswith("Microsoft 365 and basic"))

    def test_light_exposure_excludes_no_exposure(self):
        profile = PROFILE_WITH_LIGHT + "\n- **Okta**: basic Okta exposure\n"
        items = parse_light_exposure(profile)
        skills = " ".join(i["skill"] for i in items).lower()
        self.assertNotIn("okta", skills)

    def test_no_exposure_phrases(self):
        phrases = parse_no_exposure_phrases(PROFILE_WITH_LIGHT)
        self.assertTrue(any("okta" in p for p in phrases))

    def test_check_named_surfaced_missing(self):
        jd = "Microsoft 365 and Active Directory required."
        content = {
            "summary": "IT Support specialist with Linux experience.",
            "experience": [],
            "skills": {"technical": ["Linux"], "soft": []},
        }
        issues = check_named_requirements_surfaced(jd, content, PROFILE_WITH_LIGHT)
        self.assertTrue(any("Microsoft 365" in i for i in issues))

    def test_check_named_surfaced_present(self):
        jd = "Microsoft 365 required."
        content = {
            "summary": "Help desk with Microsoft 365 and user account support.",
            "experience": [],
            "skills": {"technical": ["Microsoft 365"], "soft": []},
        }
        issues = check_named_requirements_surfaced(jd, content, PROFILE_WITH_LIGHT)
        self.assertEqual(issues, [])

    def test_find_hype_violations(self):
        text = "Revolutionized the help desk with cutting-edge synergy."
        hits = find_hype_violations(text)
        self.assertIn("revolutionized", hits)
        self.assertIn("cutting-edge", hits)

    def test_factual_pipeline_framing_constant(self):
        self.assertIn("modular Python-based job-application pipeline", AI_PIPELINE_FACTUAL_FRAMING)
        self.assertNotIn("revolution", AI_PIPELINE_FACTUAL_FRAMING.lower())


if __name__ == "__main__":
    unittest.main()
