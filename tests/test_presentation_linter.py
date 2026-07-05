"""Deterministic presentation linter regression tests."""
from __future__ import annotations

import copy
import unittest

from job_pipeline.presentation_linter import (
    BLOCK,
    lint_cover_letter,
    lint_resume,
    cross_document_consistency,
    load_rules,
)


def _bad_resume() -> dict:
    return {
        "summary": (
            "Remote candidate with hands-on end-user support and help-desk-adjacent "
            "experience leveraging ticketing systems."
        ),
        "experience": [
            {
                "company": "BEAT THE BOMB",
                "title": "Technical Operations Manager",
                "bullets": [
                    "Managed help desk requests in a ticket system; handled support requests with clear verbal communication."
                ],
            }
        ],
        "skills": {
            "technical": [
                "Microsoft 365",
                "Windows OS troubleshooting",
                "help desk",
                "Ticketing / ITSM",
                "ITSM",
                "ticketing",
                "Wireshark (study)",
            ],
            "soft": ["documentation"],
        },
        "projects": [],
    }


def _clean_resume() -> dict:
    return {
        "summary": (
            "Service Desk Technician candidate with hands-on end-user support, "
            "Windows troubleshooting, and ticketing experience."
        ),
        "experience": [
            {
                "company": "BEAT THE BOMB",
                "title": "Technical Operations Manager",
                "bullets": [
                    "Managed help desk requests in a ticket system and documented resolution steps.",
                    "Authored SOPs and runbooks for recurring incidents.",
                    "Supported Windows endpoints and PC hardware in a live facility environment.",
                ],
            }
        ],
        "skills": {
            "technical": [
                "Microsoft 365",
                "Windows OS Troubleshooting",
                "Help Desk Support",
                "Ticketing/ITSM",
            ],
            "soft": ["Documentation", "Verbal Communication"],
        },
        "projects": [],
    }


class TestPresentationCasing(unittest.TestCase):
    def test_mixed_case_skills_canonicalized(self):
        result = lint_resume(_bad_resume(), job_title="Service Desk Technician")
        tech = result.content["skills"]["technical"]
        self.assertIn("Windows OS Troubleshooting", tech)
        self.assertIn("Help Desk Support", tech)
        self.assertNotIn("help desk", tech)


class TestPresentationSynonymDedupe(unittest.TestCase):
    def test_ticketing_variants_merge_once(self):
        result = lint_resume(_bad_resume(), job_title="Service Desk Technician")
        tech = result.content["skills"]["technical"]
        ticketing = [s for s in tech if "ticketing" in s.lower() or s == "ITSM"]
        self.assertEqual(len(ticketing), 1)
        self.assertEqual(ticketing[0], "Ticketing/ITSM")
        joined = "/".join(tech)
        self.assertNotIn("Ticketing/ITSM/Ticketing", joined)


class TestPresentationSemicolonSplit(unittest.TestCase):
    def test_semicolon_bullet_splits(self):
        result = lint_resume(_bad_resume(), job_title="Service Desk Technician")
        bullets = result.content["experience"][0]["bullets"]
        self.assertGreaterEqual(len(bullets), 2)
        self.assertTrue(any("semicolon_bullet" in n for n in result.notes))


class TestPresentationSummaryRetitle(unittest.TestCase):
    def test_remote_candidate_replaced(self):
        result = lint_resume(_bad_resume(), job_title="Service Desk Technician")
        summary = result.content["summary"]
        self.assertNotIn("Remote candidate", summary)
        self.assertTrue(summary.lower().startswith("service desk technician candidate"))

    def test_pasted_jd_title_replaced(self):
        content = {
            "summary": (
                "Service Desk Technician - Digitech - Remote focused on first-line "
                "end-user support and Windows troubleshooting."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        result = lint_resume(
            content, job_title="Service Desk Technician - Digitech - Remote"
        )
        summary = result.content["summary"]
        self.assertIn("Service Desk Technician candidate", summary)
        self.assertNotIn("Digitech - Remote focused", summary)


class TestPresentationBannedPhrases(unittest.TestCase):
    def test_hedge_and_hype_flagged_or_fixed(self):
        result = lint_resume(_bad_resume(), job_title="Service Desk Technician")
        notes = " ".join(result.notes).lower()
        self.assertTrue(
            "help-desk-adjacent" not in result.content["summary"].lower()
            or "help-desk-adjacent" in notes
        )


class TestPresentationStudyTag(unittest.TestCase):
    def test_study_tag_dropped_without_jd(self):
        result = lint_resume(_bad_resume(), job_title="Service Desk Technician", jd_text="help desk")
        tech = result.content["skills"]["technical"]
        self.assertFalse(any("wireshark" in s.lower() for s in tech))


class TestPresentationCoverLetter(unittest.TestCase):
    def test_missing_company_blocks(self):
        cl = {
            "opening": "I am writing to apply for this position.",
            "body_paragraphs": ["I handled tickets at BTB."],
            "closing": "Thank you for your time and consideration.",
        }
        result = lint_cover_letter(
            cl, company="Sarnova HC, LLC", role="Service Desk Technician"
        )
        self.assertTrue(any(f.severity == BLOCK for f in result.findings))


class TestPresentationCrossDoc(unittest.TestCase):
    def test_yoe_mismatch_warns(self):
        resume = {"summary": "Technician with 3+ years of support.", "experience": []}
        cl = {
            "opening": "Hello.",
            "body_paragraphs": ["I bring 5+ years of help desk experience."],
            "closing": "Thanks.",
        }
        findings = cross_document_consistency(
            resume, cl, role="Service Desk Technician", company="Acme"
        )
        self.assertTrue(any(f.rule_id == "cross_doc_yoe_mismatch" for f in findings))


class TestPresentationCleanNoop(unittest.TestCase):
    def test_clean_resume_zero_penalty(self):
        before = copy.deepcopy(_clean_resume())
        result = lint_resume(before, job_title="Service Desk Technician")
        self.assertEqual(result.penalty, 0.0)
        autofixes = [f for f in result.findings if f.severity == "autofix"]
        self.assertEqual(len(autofixes), 0)


class TestPresentationNoCrash(unittest.TestCase):
    def test_malformed_content_returns_safe(self):
        result = lint_resume({"error": "fail"}, job_title="X")
        self.assertEqual(result.penalty, 0.0)
        result2 = lint_resume({}, job_title="")
        self.assertIsInstance(result2.content, dict)


class TestRulesLoad(unittest.TestCase):
    def test_yaml_loads(self):
        rules = load_rules(force_reload=True)
        self.assertIn("canonical_casing", rules)
        self.assertIn("banned_phrases", rules)


if __name__ == "__main__":
    unittest.main()
