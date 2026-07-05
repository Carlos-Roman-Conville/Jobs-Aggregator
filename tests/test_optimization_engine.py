"""Tests for optimization engine components."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.anti_fluff import find_red_flags, strip_anti_fluff_in_text  # noqa: E402
from job_pipeline.ats_parser_check import check_extracted_resume_text  # noqa: E402
from job_pipeline.evidence_db import (  # noqa: E402
    load_evidence_db,
    match_employer_key,
    apply_parser_safe_experience,
)
from job_pipeline.jd_analysis import build_role_thesis, parse_job_description  # noqa: E402
from job_pipeline.resume_optimizer import (  # noqa: E402
    compress_skills_extended,
    opt_enabled,
    run_resume_optimization_pipeline,
)
from job_pipeline.rubric_scorer import score_resume_rubric  # noqa: E402
from job_pipeline.truth_classifier import (  # noqa: E402
    ADJACENT,
    DIRECT,
    NOT_TRUE,
    classify_jd_requirements,
)


PROFILE = """
Help desk support with onboarding documentation and MFA troubleshooting.
Office 365 user support — no Active Directory administration.
"""

JD = """
Help Desk Technician — 3+ years required.
Must have Microsoft 365, ticketing/ITSM, user onboarding, MFA/SSO support.
We value ownership, impact, and scaling our remote workforce.
"""


class TestEvidenceDb(unittest.TestCase):
    def test_loads_beat_the_bomb(self):
        db = load_evidence_db()
        self.assertIn("employers", db)
        self.assertIn("BEAT_THE_BOMB", db["employers"])

    def test_match_employer(self):
        self.assertEqual(match_employer_key("BEAT THE BOMB"), "BEAT_THE_BOMB")

    def test_parser_safe_experience(self):
        content = {
            "experience": [
                {
                    "title": "Systems Administrator",
                    "company": "BEAT THE BOMB",
                    "bullets": ["Supported users."],
                }
            ]
        }
        out, notes = apply_parser_safe_experience(content)
        self.assertEqual(out["experience"][0]["title"], "Technical Operations Manager")
        self.assertTrue(notes)


class TestTruthClassifier(unittest.TestCase):
    def test_classifies_m365_and_ad(self):
        classes = classify_jd_requirements(JD, PROFILE)
        by_label = {c["label"]: c["level"] for c in classes}
        self.assertIn("Microsoft 365", by_label)
        if "Active Directory" in by_label:
            self.assertIn(by_label["Active Directory"], (NOT_TRUE, ADJACENT))


class TestAntiFluff(unittest.TestCase):
    def test_strips_revolutionized(self):
        fixed, notes = strip_anti_fluff_in_text("Revolutionized the help desk workflow.")
        self.assertNotIn("Revolutionized", fixed)
        self.assertTrue(notes)

    def test_finds_confident_in_my_ability(self):
        self.assertIn("confident in my ability", find_red_flags("I am confident in my ability to lead."))

    def test_strips_supported_x_support_doubleup(self):
        # Sarnova/Digitech leak: LLM fronts "user account support" with "Supported".
        fixed, _ = strip_anti_fluff_in_text(
            "Supported user account support, onboarding workflows, and access-related troubleshooting."
        )
        self.assertNotIn("account support,", fixed)
        self.assertIn("Supported user account, onboarding workflows", fixed)

    def test_strips_provided_x_support_work(self):
        fixed, _ = strip_anti_fluff_in_text(
            "Provided user account support work and access tickets."
        )
        self.assertIn("Provided user account work", fixed)

    def test_preserves_legitimate_technical_support_phrasing(self):
        # Must NOT damage legitimate phrases where "support" is the real noun.
        original = "Supported colleagues with technical support during outages."
        fixed, _ = strip_anti_fluff_in_text(original)
        self.assertEqual(fixed, original)

        original2 = "Provided end-user mobile support for iOS devices."
        fixed2, _ = strip_anti_fluff_in_text(original2)
        self.assertEqual(fixed2, original2)

    def test_rewrites_handed_scripts_to_provided(self):
        # Cellular Sales leak: career_master shorthand "ran handed scripts"
        # surfaced verbatim in resume + cover letter; reads awkwardly.
        fixed, _ = strip_anti_fluff_in_text(
            "Ran handed MySQL scripts to produce escalation reports."
        )
        self.assertNotIn("handed MySQL", fixed)
        self.assertIn("provided MySQL", fixed)

        fixed2, _ = strip_anti_fluff_in_text(
            "NAS restore work and handed MySQL scripts for reports."
        )
        self.assertNotIn("handed MySQL", fixed2)
        self.assertIn("provided MySQL", fixed2)

    def test_preserves_handed_down_idiom(self):
        # "handed-down" (hyphenated, different idiom) must not be stripped.
        original = "Used a handed-down laptop from the previous tech."
        fixed, _ = strip_anti_fluff_in_text(original)
        self.assertEqual(fixed, original)

    def test_strips_as_documented_in_prior_work_hedge(self):
        # Arize leak: LLM hedged a keyword surfacing with
        # "...brings practical support for VPN, as documented in prior technical operations work."
        fixed, _ = strip_anti_fluff_in_text(
            "...brings practical support for VPN, as documented in prior technical operations work."
        )
        self.assertNotIn("as documented", fixed)
        self.assertNotIn("operations work", fixed)
        self.assertIn("VPN", fixed)

        fixed2, _ = strip_anti_fluff_in_text(
            "Supports MFA/SSO concepts, as evidenced in previous IT support roles."
        )
        self.assertNotIn("as evidenced", fixed2)
        self.assertIn("MFA/SSO", fixed2)

    def test_strips_common_llm_buzzwords(self):
        cases = [
            ("Achieved goals in order to improve.", "Achieved goals to improve."),
            ("Used a wide range of tools.", "Used many tools."),
            ("Successfully implemented the rollout.", "implemented the rollout."),
            ("Detail-oriented professional", "thorough professional"),  # red flag handled elsewhere too
            ("Going forward, we will plan.", ", we will plan."),
        ]
        for src, expected_substr in cases:
            out, _ = strip_anti_fluff_in_text(src)
            self.assertNotEqual(out, src, f"should have been changed: {src}")

    def test_strips_defensive_background_hedge(self):
        # Pure IT CUSO leak — "While my background is not from a traditional MSP..."
        fixed, _ = strip_anti_fluff_in_text(
            "While my background is not from a traditional MSP, I have repeatedly worked in support environments."
        )
        self.assertNotIn("While my background is not from", fixed)
        self.assertIn("I have repeatedly worked", fixed)

    def test_strips_at_the_user_level_only_defensive(self):
        # U.S. Courts cover letter leak: "incidents at the user level only when they came up."
        fixed, _ = strip_anti_fluff_in_text(
            "Handled printer/copier incidents at the user level only when they came up."
        )
        self.assertNotIn("at the user level only", fixed)
        self.assertIn("Handled printer/copier incidents when they came up", fixed)

        fixed2, _ = strip_anti_fluff_in_text(
            "Resolved tickets at the end-user level only across two facilities."
        )
        self.assertNotIn("end-user level only", fixed2)
        self.assertIn("Resolved tickets across two facilities", fixed2)

    def test_preserves_legitimate_documented_phrasing(self):
        # "Documented all the work I did" is legitimate — must not match the
        # "as documented in prior X work" hedge pattern.
        original = "Documented all the work I did across BTB."
        fixed, _ = strip_anti_fluff_in_text(original)
        self.assertEqual(fixed, original)

    def test_replaces_in_my_most_recent_operations_work_at_company(self):
        original = (
            "In my most recent operations work at BEAT THE BOMB, "
            "I also handled help desk ticket requests."
        )
        fixed, notes = strip_anti_fluff_in_text(original)
        self.assertIn("At BEAT THE BOMB", fixed)
        self.assertNotIn("In my most recent operations work", fixed)
        self.assertTrue(notes)


class TestRubricScorer(unittest.TestCase):
    def test_scores_reasonable_draft(self):
        content = {
            "summary": "IT Help Desk Specialist with Microsoft 365 and ticketing experience.",
            "experience": [
                {
                    "title": "Technical Operations Manager",
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Resolved Tier 1-2 tickets and documented SOPs.",
                        "Reduced guest wait time by 75% (10 to 2.5 min).",
                        "Supported 20-30 staff per shift with RustDesk remote admin.",
                    ],
                }
            ],
            "skills": {"technical": ["Microsoft 365", "Ticketing", "MFA/SSO"], "soft": ["Communication"]},
            "projects": [],
        }
        jd = parse_job_description(JD)
        classes = classify_jd_requirements(JD, PROFILE)
        score = score_resume_rubric(
            content,
            JD,
            PROFILE,
            classifications=classes,
            jd_analysis=jd,
            thesis=build_role_thesis("Help Desk Technician", jd),
            job_title="Help Desk Technician",
        )
        self.assertGreaterEqual(score["total"], 55)
        self.assertIn("coverage", score["breakdown"])


class TestResumeOptimizer(unittest.TestCase):
    def test_pipeline_runs_when_enabled(self):
        content = {
            "summary": "Help desk support professional.",
            "experience": [
                {
                    "title": "Sysadmin",
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Leveraged ticketing systems to revolutionize support.",
                        "Supported Windows and Linux endpoints.",
                        "Authored SOPs and runbooks.",
                        "Resolved incidents under pressure.",
                        "Coordinated team workflows.",
                        "Maintained hardware.",
                        "Improved onboarding docs.",
                    ],
                }
            ],
            "skills": {
                "technical": ["Microsoft 365"] + [f"Skill{i}" for i in range(30)],
                "soft": [f"Soft{i}" for i in range(20)],
            },
            "projects": [],
        }
        with mock.patch.dict(os.environ, {"RESUME_OPT_ENABLED": "1", "RESUME_OPT_FULL": "0"}):
            self.assertTrue(opt_enabled())
            result = run_resume_optimization_pipeline(
                content,
                JD,
                PROFILE,
                job_title="Help Desk Technician",
                company="Acme",
            )
        opt = result["optimization"]
        self.assertNotIn("skipped", opt)
        self.assertIn("score", opt)
        self.assertIn("thesis", opt)
        blob = json.dumps(result["content"]).lower()
        self.assertNotIn("leveraged", blob)
        self.assertNotIn("revolutionize", blob)


class TestAtsParserCheck(unittest.TestCase):
    def test_detects_short_text(self):
        r = check_extracted_resume_text("Hi")
        self.assertFalse(r["ok"])

    def test_passes_structured_text(self):
        text = """
        BEAT THE BOMB
        Technical Operations Manager
        Philadelphia, PA
        2024 - 2026
        - Supported Microsoft 365 users
        - Reduced wait time by 75%
        """
        r = check_extracted_resume_text(text, expected_companies=["BEAT THE BOMB"])
        self.assertTrue(r["ok"])

    def test_experience_companies_from_content(self):
        from job_pipeline.ats_parser_check import experience_companies_from_content

        companies = experience_companies_from_content(
            {
                "experience": [
                    {"company": "BEAT THE BOMB", "bullets": []},
                    {"company": "1-800-GOT-JUNK", "bullets": []},
                ]
            }
        )
        self.assertEqual(companies, ["BEAT THE BOMB", "1-800-GOT-JUNK"])

    def test_does_not_expect_target_job_company(self):
        """Regression: applying to Sarnova must not require Sarnova in resume text."""
        text = """
        BEAT THE BOMB
        Technical Operations Manager
        2024 - 2026
        """
        r = check_extracted_resume_text(
            text,
            expected_companies=["BEAT THE BOMB"],
        )
        warnings = r.get("warnings") or []
        self.assertFalse(any("Sarnova" in w for w in warnings))
        self.assertFalse(any("company name not found" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
