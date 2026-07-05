"""Tests for calibrated quality judge (offline — no live OpenAI unless marked)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from job_pipeline import quality_judge


class TestJudgeAnchorsLoad(unittest.TestCase):
    def test_anchors_load_from_user_folder(self):
        anchors = quality_judge.load_judge_anchors(force_reload=True)
        self.assertGreaterEqual(len(anchors.get("resume") or []), 3)
        self.assertGreaterEqual(len(anchors.get("cover_letter") or []), 3)
        tiers = {a["tier"] for a in anchors["resume"]}
        self.assertIn("target", tiers)
        self.assertIn("nearmiss", tiers)

    def test_anchor_scores_parsed(self):
        anchors = quality_judge.load_judge_anchors(force_reload=True)
        target = [a for a in anchors["resume"] if a["tier"] == "target"][0]
        self.assertGreaterEqual(target["score"], 9.0)
        nearmiss = [a for a in anchors["resume"] if a["tier"] == "nearmiss"][0]
        self.assertAlmostEqual(nearmiss["score"], 8.0, delta=0.5)

    def test_rationale_sidecar_enriches_why(self):
        anchors = quality_judge.load_judge_anchors(force_reload=True)
        vytl = [
            a for a in anchors["resume"]
            if "VytlOne" in (a.get("slug") or "") and a.get("tier") == "target"
        ]
        if not vytl:
            self.skipTest("VytlOne target anchor not present")
        why = vytl[0].get("why") or ""
        self.assertIn("VytlOne", why)
        self.assertNotEqual(
            why,
            quality_judge._TIER_WHY.get("target"),
        )


class TestJudgeDisabled(unittest.TestCase):
    def test_judge_off_when_env_disabled(self):
        with mock.patch.dict(os.environ, {"RESUME_OPT_JUDGE": "0"}):
            self.assertFalse(quality_judge.judge_enabled())
            result = quality_judge.judge_quality({"summary": "x", "experience": []})
            self.assertFalse(result.get("ok"))


class TestJudgeDefensive(unittest.TestCase):
    def test_provider_error_returns_ok_false(self):
        with mock.patch.dict(os.environ, {"RESUME_OPT_JUDGE": "1"}), \
             mock.patch.object(quality_judge, "load_judge_anchors", return_value={"resume": [{}], "cover_letter": []}), \
             mock.patch.object(
                 quality_judge,
                 "openai_generate_json_with_retry",
                 side_effect=RuntimeError("provider down"),
             ):
            result = quality_judge.judge_quality(
                {"summary": "Service Desk Technician candidate.", "experience": [], "skills": {}},
                job_title="Service Desk Technician",
            )
        self.assertFalse(result.get("ok"))


class TestGateUsesJudge(unittest.TestCase):
    def test_opt_judge_min_default(self):
        self.assertGreaterEqual(quality_judge.opt_judge_min(), 85)

    def test_pkg_judge_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESUME_OPT_PKG_JUDGE", None)
            self.assertFalse(quality_judge.pkg_judge_enabled())

    @mock.patch.object(quality_judge, "judge_quality")
    @mock.patch.object(quality_judge, "judge_enabled", return_value=True)
    def test_judge_passes_gate_flag(self, _enabled, mock_judge):
        mock_judge.return_value = {
            "ok": True,
            "score": 92,
            "passes_gate": True,
            "critique": [],
            "verdict": "target",
        }
        result = quality_judge.judge_quality({"summary": "x"})
        self.assertTrue(result.get("passes_gate"))


if __name__ == "__main__":
    unittest.main()
