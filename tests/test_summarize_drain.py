"""Tests for summarize-all drain and ATS profile guard."""
from __future__ import annotations

import unittest
from unittest import mock

from job_pipeline import ats_score
from job_pipeline.summarize import run_summarize_all


class TestRunSummarizeAll(unittest.TestCase):
    @mock.patch("job_pipeline.summarize.run_summarize_batch")
    @mock.patch("job_pipeline.summarize.count_items_by_status")
    def test_drains_until_empty(self, mock_count, mock_batch):
        mock_count.side_effect = [10, 5, 0, 0]
        mock_batch.side_effect = [
            {
                "summarized": [1, 2, 3, 4, 5],
                "pending_review_count": 2,
                "auto_filtered_count": 3,
                "close_reason_counts": {"threshold": 3},
                "errors": [],
            },
            {
                "summarized": [6, 7],
                "pending_review_count": 1,
                "auto_filtered_count": 1,
                "close_reason_counts": {"search_preferences": 1},
                "errors": [],
            },
        ]
        out = run_summarize_all(batch_size=5, max_batches=10, max_minutes=5.0)
        self.assertEqual(out["batches"], 2)
        self.assertEqual(out["summarized"], 7)
        self.assertEqual(out["ingested_remaining"], 0)
        self.assertFalse(out["stopped_early"])

    @mock.patch("job_pipeline.summarize.run_summarize_batch")
    @mock.patch("job_pipeline.summarize.count_items_by_status")
    def test_stops_on_should_stop(self, mock_count, mock_batch):
        mock_count.return_value = 5
        mock_batch.return_value = {
            "summarized": [1],
            "pending_review_count": 1,
            "auto_filtered_count": 0,
            "close_reason_counts": {},
            "errors": [],
        }
        out = run_summarize_all(
            batch_size=50,
            max_batches=10,
            should_stop=lambda: True,
        )
        self.assertTrue(out["stopped_early"])
        self.assertEqual(out["stop_reason"], "cancelled")
        mock_batch.assert_not_called()


class TestAtsCanonicalProfile(unittest.TestCase):
    @mock.patch("job_pipeline.ats_score.load_application_assets", return_value='{"resumes": []}')
    @mock.patch("job_pipeline.bootstrap_resume_profile.load_consolidated_profile_text")
    @mock.patch("job_pipeline.bootstrap_resume_profile.load_consolidated_profile")
    def test_includes_consolidated_profile_text(self, mock_prof, mock_text, _assets):
        mock_text.return_value = "Desktop support specialist with Active Directory and help desk experience. " * 3
        mock_prof.return_value = {
            "headline": "IT Support",
            "skills": {"technical": ["Active Directory", "Windows 10"]},
        }
        blob, note = ats_score.build_canonical_resume_text()
        self.assertIn("Desktop support", blob)
        self.assertIn("consolidated_profile.md", note)
        self.assertGreater(len(blob), ats_score._MIN_CANONICAL_CHARS)

    @mock.patch("job_pipeline.ats_score.load_application_assets", return_value='{"resumes": []}')
    @mock.patch("job_pipeline.bootstrap_resume_profile.load_consolidated_profile_text", return_value="")
    @mock.patch("job_pipeline.bootstrap_resume_profile.load_consolidated_profile", return_value={})
    def test_skips_ats_when_profile_thin(self, *_mocks):
        blob, note = ats_score.build_canonical_resume_text()
        self.assertLess(len(blob), ats_score._MIN_CANONICAL_CHARS)
        self.assertIn("thin_profile", note)
        out = ats_score.compute_ats_overlap(
            "Windows Active Directory help desk troubleshooting",
            canonical_resume_blob=blob,
            resume_skill_terms=["Active Directory"],
        )
        self.assertTrue(out.get("ats_skipped"))


if __name__ == "__main__":
    unittest.main()
