"""Tests for grammar proofread pass and pre-export gates."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from job_pipeline import grammar_proofread, integrity_guards


SAMPLE_RESUME = {
    "summary": "Help desk technician with ticketing experience.",
    "experience": [
        {
            "company": "BEAT THE BOMB",
            "bullets": [
                "I also across hardware, operating systems, and local network layers."
            ],
        }
    ],
    "skills": {"technical": ["Ticketing/ITSM"], "soft": []},
}


SAMPLE_CL = {
    "opening": "Hello.",
    "body_paragraphs": ["I also across hardware, operating systems, and network layers."],
    "closing": "Thanks.",
}


class TestGrammarGuardsResume(unittest.TestCase):
    def test_repairs_broken_bullet(self):
        content = {
            "summary": "Service desk candidate.",
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "I also across hardware, operating systems, and local network layers."
                    ],
                }
            ],
        }
        notes = integrity_guards.run_grammar_guards_resume(content)
        bullet = content["experience"][0]["bullets"][0]
        self.assertIn("resolved live incidents across", bullet)
        self.assertTrue(notes)


class TestPreExportGate(unittest.TestCase):
    def test_repairs_after_anti_fluff_simulation(self):
        content = dict(SAMPLE_CL)
        broken = (
            "At BEAT THE BOMB, I managed tickets. "
            "I also across hardware, operating systems, and network layers."
        )
        content["body_paragraphs"] = [broken]
        notes = integrity_guards.run_pre_export_guards(
            content, doc_type="cover_letter", job_title="Service Desk Technician"
        )
        body = content["body_paragraphs"][0]
        self.assertIn("resolved live incidents across", body)
        self.assertTrue(notes)

    def test_resume_pre_export_repairs_summary_and_bullets(self):
        content = {
            "summary": "I also across hardware issues daily.",
            "experience": [
                {"company": "BTB", "bullets": ["I also across network layers."]}
            ],
        }
        integrity_guards.run_pre_export_guards(content, doc_type="resume")
        self.assertIn("resolved live incidents across", content["summary"])
        self.assertIn("resolved live incidents across", content["experience"][0]["bullets"][0])


class TestScanAndRepairProse(unittest.TestCase):
    def test_collapses_double_spaces(self):
        fixed, notes = integrity_guards.scan_and_repair_prose("Handled  tickets  daily.")
        self.assertEqual(fixed, "Handled tickets daily.")
        self.assertTrue(any("whitespace" in n for n in notes))


class TestGrammarProofread(unittest.TestCase):
    def test_disabled_when_env_off(self):
        with mock.patch.dict(os.environ, {"RESUME_OPT_GRAMMAR_PASS": "0", "RESUME_OPT_CRITIQUE_LOOP": "1"}):
            self.assertFalse(grammar_proofread.grammar_pass_enabled())

    def test_proofread_applies_llm_fixes(self):
        revised = {
            "summary": "Service desk technician with ticketing and Windows support.",
            "experience": SAMPLE_RESUME["experience"],
            "skills": SAMPLE_RESUME["skills"],
        }

        with mock.patch.dict(os.environ, {"RESUME_OPT_GRAMMAR_PASS": "1", "RESUME_OPT_CRITIQUE_LOOP": "1"}), \
             mock.patch.object(grammar_proofread, "writing_providers_available", return_value=True), \
             mock.patch.object(grammar_proofread, "generate_json", return_value=revised):
            out, notes = grammar_proofread.proofread_resume_content(
                dict(SAMPLE_RESUME),
                profile_text="Profile",
                job_title="Service Desk Technician",
            )
        self.assertIn("Windows support", out["summary"])
        self.assertTrue(any("copy-edit" in n for n in notes))


class TestCoverLetterOptimizerFinalGate(unittest.TestCase):
    def test_pre_export_runs_when_critique_disabled(self):
        from job_pipeline.cover_letter_optimizer import optimize_cover_letter_content

        letter = {
            "opening": "Hello.",
            "body_paragraphs": [
                "I also across hardware, operating systems, and network layers."
            ],
            "closing": "Thanks.",
        }
        with mock.patch.dict(os.environ, {"RESUME_OPT_CRITIQUE_LOOP": "0", "RESUME_OPT_GRAMMAR_PASS": "0"}):
            result = optimize_cover_letter_content(
                dict(letter),
                job_description="JD",
                profile_text="Profile",
                job_title="Service Desk Technician",
            )
        body = " ".join(result.get("body_paragraphs") or [])
        self.assertIn("resolved live incidents across", body)


if __name__ == "__main__":
    unittest.main()
