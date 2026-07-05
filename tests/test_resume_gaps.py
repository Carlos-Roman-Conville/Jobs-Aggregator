"""Tests for resume_gaps named-requirement integration."""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.resume_gaps import detect_gaps  # noqa: E402


PROFILE = """
## Tools I have actually used
- Office 365 admin console — added/removed users for ~50 staff
"""


class TestResumeGapsNamedReq(unittest.TestCase):
    def test_detect_gaps_includes_named_req_category(self):
        jd = (
            "Responsible for user onboarding/offboarding and Active Directory. "
            "Must have VMware vSphere experience."
        )
        gaps = detect_gaps(jd, profile_text=PROFILE, use_llm=False)
        categories = {g.get("category") for g in gaps}
        self.assertIn("named_req", categories)
        reqs = " ".join(g.get("requirement", "") for g in gaps).lower()
        self.assertIn("active directory", reqs)

    def test_supported_named_req_not_gap_when_in_profile(self):
        jd = "Microsoft 365 administration required."
        gaps = detect_gaps(jd, profile_text=PROFILE, use_llm=False)
        named = [g for g in gaps if g.get("category") == "named_req" and g.get("requirement") == "Microsoft 365"]
        self.assertEqual(named, [])


if __name__ == "__main__":
    unittest.main()
