"""Unit tests for search_preferences.md parsing and scoring."""
from __future__ import annotations

import unittest

from job_pipeline.location_policy import classify_remote_hybrid_on_site
from job_pipeline.search_preferences import load_search_preferences, score_posting_against_preferences
from job_pipeline.summarize import apply_search_preferences_stage


def _post(d: dict) -> dict:
    base = {"title": "", "location": "", "salary_text": "", "description_text": "", "source": ""}
    base.update(d)
    return base


class TestSearchPreferences(unittest.TestCase):
    def test_load_search_preferences_keys_and_floors(self) -> None:
        p = load_search_preferences()
        self.assertIsInstance(p, dict)
        for k in (
            "salary_floors",
            "metro_radius_miles",
            "search_term_seeds",
            "avoid_title_re",
            "noise_body_re",
        ):
            self.assertIn(k, p)
        self.assertEqual(
            p["salary_floors"],
            {"remote": 60000, "remote_flex": 55000, "hybrid": 65000, "onsite": 70000},
        )

    def test_search_term_seeds_non_empty_from_example_md(self) -> None:
        seeds = load_search_preferences().get("search_term_seeds") or []
        self.assertGreater(len(seeds), 3)

    def test_remote_tier1_60k(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "IT Support Specialist",
                    "location": "Remote (US)",
                    "salary_text": "$62,000–$70,000/yr",
                    "description_text": (
                        "Junior to Mid to Senior promotion path. "
                        "Certification reimbursement for CompTIA and Microsoft."
                    ),
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertAlmostEqual(r["pref_multiplier"], 1.47, delta=0.08)
        self.assertIn("remote_1.25x", r["boost_signals"])
        self.assertIn("tier1_title_family_1.15x", r["boost_signals"])

    def test_hybrid_center_city(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Desktop Support Technician",
                    "location": "Philadelphia, PA",
                    "salary_text": "$66,000",
                    "description_text": "Hybrid role with office days in Center City.",
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertAlmostEqual(r["pref_multiplier"], 1.39, delta=0.08)
        self.assertIn("hybrid_in_metro_1.10x", r["boost_signals"])
        self.assertIn("tier1_title_family_1.15x", r["boost_signals"])
        self.assertTrue(any("proximity_inner_core" in b for b in r["boost_signals"]))

    def test_onsite_kop_tech_75k(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "NOC Technician",
                    "location": "King of Prussia, PA",
                    "salary_text": "$75,000",
                    "description_text": "On-site technical support role.",
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertAlmostEqual(r["pref_multiplier"], 1.22, delta=0.08)
        self.assertIn("onsite_metro_tech_ge_70k_1.05x", r["boost_signals"])
        self.assertTrue(any(b.startswith("proximity_") for b in r["boost_signals"]))

    def test_onsite_nyc_reject(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Help Desk Analyst",
                    "location": "New York, NY",
                    "salary_text": "$72,000",
                    "description_text": "On-site position in Manhattan.",
                }
            )
        )
        self.assertEqual(r["auto_close_reason"], "outside_metro")

    def test_noise_ai_trainer(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "AI Trainer",
                    "location": "Remote",
                    "salary_text": "$30/hr",
                    "description_text": "1099 contractor data annotation gig work.",
                }
            )
        )
        self.assertEqual(r["auto_close_reason"], "title_avoided")

    def test_senior_avoid(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Senior Systems Administrator",
                    "location": "Remote",
                    "salary_text": "$150,000",
                }
            )
        )
        self.assertEqual(r["auto_close_reason"], "title_avoided")

    def test_hybrid_wilmington(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Help Desk Technician",
                    "location": "Wilmington, DE",
                    "salary_text": "$68,000",
                    "description_text": "Hybrid schedule. Certification reimbursement available.",
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertAlmostEqual(r["pref_multiplier"], 1.30, delta=0.08)
        self.assertTrue(any("proximity" in b for b in r["boost_signals"]))

    def test_usajobs_vet_lane(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "IT Specialist",
                    "location": "Philadelphia, PA",
                    "salary_text": "$78,000",
                    "source": "usajobs",
                    "description_text": "On-site technical role serving federal systems.",
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertIn("vet_lane_1.08x", r["boost_signals"])

    def test_remote_low_salary(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "IT Support Technician",
                    "location": "Remote",
                    "salary_text": "$45,000",
                    "description_text": "No growth track described.",
                }
            )
        )
        self.assertEqual(r["auto_close_reason"], "salary_below_floor")

    def test_hourly_36(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Desktop Support",
                    "location": "Philadelphia, PA",
                    "salary_text": "$36 per hour",
                    "description_text": "On-site position 5 days per week.",
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertEqual(r["salary_low_usd"], 74880)
        self.assertAlmostEqual(r["pref_multiplier"], 1.33, delta=0.08)

    def test_remote_56k_growth_flex(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Junior Sysadmin",
                    "location": "Remote",
                    "salary_text": "$56,500",
                    "description_text": (
                        "Promotion path to mid and senior roles. Mentorship program. "
                        "Certification reimbursement for CompTIA."
                    ),
                }
            )
        )
        self.assertIsNone(r["auto_close_reason"])
        self.assertAlmostEqual(r["pref_multiplier"], 1.50, delta=0.08)

    def test_telework_phrase_classifies_hybrid(self) -> None:
        cls = classify_remote_hybrid_on_site(
            "IT Specialist",
            "Washington, DC",
            "This position is telework eligible with some office reporting.",
        )
        self.assertEqual(cls, "hybrid")

    def test_intern_in_title_triggers_noise_filtered(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Customer Onboarding & Delivery Intern",
                    "location": "Remote",
                    "salary_text": "$65,000",
                    "description_text": "Junior role helping customers with onboarding.",
                }
            )
        )
        self.assertEqual(r.get("auto_close_reason"), "noise_filtered")

    def test_internal_word_not_noise(self) -> None:
        r = score_posting_against_preferences(
            _post(
                {
                    "title": "Internal IT Support Specialist",
                    "location": "Remote",
                    "salary_text": "$65,000",
                    "description_text": "Support internal employees with laptops and access.",
                }
            )
        )
        self.assertNotEqual(r.get("auto_close_reason"), "noise_filtered")

    def test_apply_search_preferences_stage_respects_enabled_off(self) -> None:
        cfg = {"filters": {"search_preferences": {"enabled": False}}}
        combined, card, rej, code, fit_raw = apply_search_preferences_stage(
            cfg,
            title="Senior Systems Administrator",
            description_text="",
            location="Remote",
            salary_text="$200,000",
            source="",
            combined_after_location=0.5,
            loc_reject=False,
        )
        self.assertEqual(combined, 0.5)
        self.assertAlmostEqual(fit_raw, 0.5, delta=0.001)
        self.assertFalse(rej)
        self.assertIsNone(code)
        self.assertEqual(card.get("pref_multiplier"), 1.0)
        self.assertTrue(card.get("config_disabled"))


if __name__ == "__main__":
    unittest.main()
