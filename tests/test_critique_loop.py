"""Tests for the generate -> critique -> revise loop.

We mock generate_json so the tests don't hit any LLM provider.
The key behaviors under test:

- Early stop when critique returns no high-severity issues.
- Iteration cap (3 by default).
- Graceful failure when generate_json raises (returns input content unchanged).
- post_revise_hook is invoked between iterations.
- Master switch RESUME_OPT_CRITIQUE_LOOP=0 disables the loop.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline import critique_loop  # noqa: E402
from job_pipeline.critique_loop import (  # noqa: E402
    critique_loop_enabled,
    run_cover_letter_critique_loop,
    run_resume_critique_loop,
)
from job_pipeline.llm_provider import LLMWritingError  # noqa: E402


SAMPLE_RESUME = {
    "summary": "Service Desk Technician with help desk experience.",
    "experience": [
        {
            "company": "BEAT THE BOMB",
            "position": "Technical Operations Manager",
            "bullets": ["Handled tickets.", "Wrote SOPs."],
        }
    ],
    "skills": {"technical": ["Ticketing/ITSM", "Microsoft 365"], "soft": ["Communication"]},
    "projects": [],
}

SAMPLE_COVER_LETTER = {
    "opening": "Hello Acme Co.",
    "body_paragraphs": ["I worked at BTB.", "I documented SOPs."],
    "closing": "I'd welcome a conversation.",
}


def _fake_critique_clean(*_args, **_kwargs):
    return {"issues": [], "ready_to_ship": True}


def _fake_critique_one_high(*_args, **_kwargs):
    return {
        "issues": [
            {
                "severity": "high",
                "location": "summary",
                "category": "vague_claim",
                "snippet": "Service Desk Technician with help desk experience.",
                "fix_hint": "Add concrete proof — tools, numbers, scale.",
            }
        ],
        "ready_to_ship": False,
    }


def _fake_revise_applied(content, **_kwargs):  # noqa: ARG001
    # Pretend the LLM revised the summary.
    updated = dict(content)
    updated["summary"] = "Service Desk Technician with 3 years end-user support, Microsoft 365, and SOPs."
    return updated


class _EnableLoopMixin:
    """Force RESUME_OPT_CRITIQUE_LOOP=1 and writing_providers_available=True for the test."""

    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"RESUME_OPT_CRITIQUE_LOOP": "1"})
        self._env.start()
        self._wpa = mock.patch.object(critique_loop, "writing_providers_available", return_value=True)
        self._wpa.start()

    def tearDown(self):
        self._wpa.stop()
        self._env.stop()
        super().tearDown()


class TestCritiqueLoopMasterSwitch(unittest.TestCase):
    def test_disabled_via_env_returns_content_unchanged(self):
        with mock.patch.dict(os.environ, {"RESUME_OPT_CRITIQUE_LOOP": "0"}):
            self.assertFalse(critique_loop_enabled())
            content, reports, notes = run_resume_critique_loop(
                SAMPLE_RESUME, job_description="JD", profile_text="P"
            )
            self.assertEqual(reports, [])
            self.assertEqual(content, SAMPLE_RESUME)

    def test_enabled_by_default(self):
        # Clear env var so default kicks in.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESUME_OPT_CRITIQUE_LOOP", None)
            self.assertTrue(critique_loop_enabled())


class TestCritiqueLoopEarlyStop(_EnableLoopMixin, unittest.TestCase):
    def test_clean_critique_stops_after_one_iteration(self):
        # generate_json returns a clean critique on first call.
        with mock.patch.object(critique_loop, "generate_json", side_effect=_fake_critique_clean) as gj:
            content, reports, notes = run_resume_critique_loop(
                SAMPLE_RESUME, job_description="JD", profile_text="P", job_title="Service Desk Technician"
            )
        self.assertEqual(len(reports), 1)
        self.assertEqual(gj.call_count, 1)  # only the critique call; no revise
        self.assertTrue(any("no high-severity" in n for n in notes))
        # Content unchanged because no revise was needed.
        self.assertEqual(content["summary"], SAMPLE_RESUME["summary"])


class TestCritiqueLoopIteration(_EnableLoopMixin, unittest.TestCase):
    def test_one_high_then_clean_runs_two_iterations(self):
        # Iter 1: critique flags high issue, revise pass updates content.
        # Iter 2: critique returns clean, loop stops.
        call_log = []

        def fake_generate(*_args, label="", **_kwargs):
            call_log.append(label)
            if "critique_1" in label:
                return _fake_critique_one_high()
            if "revise_1" in label:
                # Mimic _fake_revise_applied — but generate_json returns the JSON
                # the revise pass would parse, so return revised payload.
                return {
                    "summary": "Service Desk Technician with 3 years end-user support, Microsoft 365, and SOPs.",
                }
            if "critique_2" in label:
                return _fake_critique_clean()
            return {}

        with mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            content, reports, notes = run_resume_critique_loop(
                SAMPLE_RESUME, job_description="JD", profile_text="P", job_title="Service Desk Technician"
            )

        # Two critique reports — iter1 had issues, iter2 clean.
        self.assertEqual(len(reports), 2)
        self.assertEqual(
            call_log,
            ["resume_critique_critique_1", "resume_critique_revise_1", "resume_critique_critique_2"],
        )
        # Summary was revised.
        self.assertIn("3 years end-user support", content["summary"])

    def test_max_iterations_cap(self):
        # Every critique returns a high issue — should cap at 3 iterations.
        def fake_generate(*_args, label="", **_kwargs):
            if "critique" in label:
                return _fake_critique_one_high()
            if "revise" in label:
                return {"summary": "still vague summary"}
            return {}

        with mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            content, reports, notes = run_resume_critique_loop(
                SAMPLE_RESUME, job_description="JD", profile_text="P", max_iterations=3
            )
        self.assertEqual(len(reports), 3)
        # The else-branch on the for-loop appends a "hit max iterations" note.
        self.assertTrue(any("max iterations" in n for n in notes))


class TestCritiqueLoopFailure(_EnableLoopMixin, unittest.TestCase):
    def test_llm_failure_returns_content_unchanged(self):
        def fake_generate(*_args, **_kwargs):
            raise LLMWritingError("provider down")

        with mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            content, reports, notes = run_resume_critique_loop(
                SAMPLE_RESUME, job_description="JD", profile_text="P"
            )
        self.assertEqual(reports, [])
        self.assertEqual(content, SAMPLE_RESUME)
        self.assertTrue(any("failed" in n for n in notes))

    def test_no_writing_provider_skips_loop(self):
        # Override the mixin's mock to return False for this test.
        with mock.patch.object(critique_loop, "writing_providers_available", return_value=False):
            content, reports, notes = run_resume_critique_loop(
                SAMPLE_RESUME, job_description="JD", profile_text="P"
            )
        self.assertEqual(reports, [])
        self.assertEqual(content, SAMPLE_RESUME)
        self.assertTrue(any("no writing provider" in n for n in notes))


class TestCritiqueLoopPostReviseHook(_EnableLoopMixin, unittest.TestCase):
    def test_post_revise_hook_called_after_each_revise(self):
        hook_calls = []

        def hook(c):
            hook_calls.append(dict(c))

        def fake_generate(*_args, label="", **_kwargs):
            if "critique_1" in label:
                return _fake_critique_one_high()
            if "revise_1" in label:
                return {"summary": "post-revise summary"}
            return _fake_critique_clean()

        with mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            run_resume_critique_loop(
                SAMPLE_RESUME,
                job_description="JD",
                profile_text="P",
                post_revise_hook=hook,
            )
        # Hook ran once — after the single revise pass.
        self.assertEqual(len(hook_calls), 1)
        self.assertIn("post-revise summary", hook_calls[0]["summary"])


class TestCoverLetterCritiqueLoop(_EnableLoopMixin, unittest.TestCase):
    def test_cover_letter_loop_uses_cover_letter_keys(self):
        # Verify the revise pass only updates cover-letter keys (no resume keys leak in).
        def fake_generate(*_args, label="", **_kwargs):
            if "critique_1" in label:
                return {
                    "issues": [
                        {
                            "severity": "high",
                            "location": "closing",
                            "category": "duplicate_closing",
                            "snippet": "I'd welcome a conversation.",
                            "fix_hint": "Combine into one closing with a thank-you.",
                        }
                    ],
                    "ready_to_ship": False,
                }
            if "revise_1" in label:
                return {
                    "closing": "I'd welcome a conversation. Thank you for your time.",
                    # Bogus extra key — must be ignored by the merge.
                    "summary": "this should not appear",
                }
            return _fake_critique_clean()

        with mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            content, reports, notes = run_cover_letter_critique_loop(
                SAMPLE_COVER_LETTER, job_description="JD", profile_text="P"
            )
        # Closing was updated.
        self.assertIn("Thank you", content["closing"])
        # Resume-only "summary" key did NOT leak into the cover letter content.
        self.assertNotIn("summary", content)


class TestCoverLetterPostReviseHook(unittest.TestCase):
    def test_post_revise_strips_fluff_and_shared_phrases(self):
        from job_pipeline.cover_letter_optimizer import optimize_cover_letter_content

        resume_content = {
            "summary": "Worked in a small-shop environment.",
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        letter = {
            "opening": "Hello.",
            "body_paragraphs": [
                "In my most recent operations work at BEAT THE BOMB, I handled tickets.",
                "My BTB work was in a small-shop environment with daily tickets.",
            ],
            "closing": "Thanks.",
        }

        def fake_generate(*_args, label="", **_kwargs):
            if "critique_1" in label:
                return {
                    "issues": [
                        {
                            "severity": "high",
                            "location": "body_paragraphs[0]",
                            "category": "repetition",
                            "snippet": "small-shop environment",
                            "fix_hint": "Remove repeated phrasing.",
                        }
                    ],
                    "ready_to_ship": False,
                }
            if "revise_1" in label:
                return {
                    "opening": "Hello.",
                    "body_paragraphs": [
                        "In my most recent operations work at BEAT THE BOMB, I handled tickets.",
                        "My BTB work was in a small-shop environment with daily tickets.",
                    ],
                    "closing": "Thanks.",
                }
            return {"issues": [], "ready_to_ship": True}

        with mock.patch.dict(os.environ, {"RESUME_OPT_CRITIQUE_LOOP": "1"}), \
             mock.patch.object(critique_loop, "writing_providers_available", return_value=True), \
             mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            result = optimize_cover_letter_content(
                dict(letter),
                job_description="Service desk JD",
                profile_text="Profile",
                job_title="Service Desk Technician Tier 1",
                company="Logicalis",
                resume_content=resume_content,
            )

        joined = " ".join(result.get("body_paragraphs") or [])
        self.assertNotIn("In my most recent operations work", joined)
        self.assertNotIn("small-shop environment", joined)


class TestCritiqueModelSelection(unittest.TestCase):
    def test_critique_uses_dedicated_role(self):
        # The critique pass must request the "critique" role from generate_json
        # so it gets the cheaper model. The revise pass uses "tailor".
        captured_roles = []

        def fake_generate(role, *_args, label="", **_kwargs):
            captured_roles.append((role, label))
            if "critique_1" in label:
                return {
                    "issues": [
                        {"severity": "high", "location": "summary", "category": "vague_claim",
                         "snippet": "x", "fix_hint": "y"}
                    ],
                    "ready_to_ship": False,
                }
            if "revise_1" in label:
                return {"summary": "revised"}
            return {"issues": [], "ready_to_ship": True}

        with mock.patch.dict(os.environ, {"RESUME_OPT_CRITIQUE_LOOP": "1"}), \
             mock.patch.object(critique_loop, "writing_providers_available", return_value=True), \
             mock.patch.object(critique_loop, "generate_json", side_effect=fake_generate):
            run_resume_critique_loop(SAMPLE_RESUME, job_description="JD", profile_text="P")

        # First call is a critique (role=critique). Revise (role=tailor) follows.
        self.assertEqual(captured_roles[0][0], "critique")
        self.assertEqual(captured_roles[1][0], "tailor")


class TestCritiqueLoopErrorContent(unittest.TestCase):
    def test_content_with_error_returns_immediately(self):
        # Even with the loop enabled and a working LLM mock, errored content
        # must short-circuit before any LLM call.
        with mock.patch.dict(os.environ, {"RESUME_OPT_CRITIQUE_LOOP": "1"}), \
             mock.patch.object(critique_loop, "writing_providers_available", return_value=True), \
             mock.patch.object(critique_loop, "generate_json") as gj:
            content, reports, notes = run_resume_critique_loop(
                {"error": "tailor_failed"}, job_description="JD", profile_text="P"
            )
        gj.assert_not_called()
        self.assertEqual(reports, [])


class TestCritiqueRubricGrammar(unittest.TestCase):
    def test_resume_rubric_includes_grammar(self):
        joined = " ".join(critique_loop.RESUME_CRITIQUE_RUBRIC).lower()
        self.assertIn("missing verbs", joined)

    def test_cover_letter_default_max_iterations_matches_resume(self):
        import inspect
        sig = inspect.signature(run_cover_letter_critique_loop)
        default = sig.parameters["max_iterations"].default
        self.assertEqual(default, critique_loop.DEFAULT_MAX_ITERATIONS)


if __name__ == "__main__":
    unittest.main()
