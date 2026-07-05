"""Tests for autofill profile export."""
from __future__ import annotations

import unittest

from job_pipeline.autofill_profile import _parse_ym, _split_name, build_autofill_profile


class TestAutofillProfile(unittest.TestCase):
    def test_split_name(self) -> None:
        bits = _split_name("CARLOS ROMAN-CONVILLE")
        self.assertEqual(bits["first_name"], "Carlos")
        self.assertIn("Roman", bits["last_name"])

    def test_parse_ym(self) -> None:
        mm, yy, slash, disp = _parse_ym("2024-09")
        self.assertEqual(mm, "09")
        self.assertEqual(yy, "2024")
        self.assertEqual(slash, "09/2024")
        self.assertIn("September", disp)

    def test_build_has_experience(self) -> None:
        profile = build_autofill_profile(
            {
                "name": "Test User",
                "contact": {"email": "a@b.com", "phone": "555-111-2222", "location": "Philadelphia, PA"},
                "experience": [
                    {
                        "company": "Acme",
                        "title": "Tech",
                        "start_date": "2024-01",
                        "end_date": "2025-02",
                        "bullets": ["Did support"],
                    }
                ],
                "education": [],
            }
        )
        self.assertEqual(profile["contact"]["email"], "a@b.com")
        self.assertEqual(len(profile["experience"]), 1)
        self.assertEqual(profile["experience"][0]["company"], "Acme")

    def test_references_from_file(self) -> None:
        profile = build_autofill_profile({"name": "Test", "contact": {}, "experience": [], "education": []})
        self.assertGreaterEqual(len(profile.get("references") or []), 2)
        ian = profile["references"][0]
        self.assertIn("Ian", ian["name"])
        self.assertEqual(ian["email"], "ianquirk91@gmail.com")


if __name__ == "__main__":
    unittest.main()
