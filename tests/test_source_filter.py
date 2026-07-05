"""Dashboard source label helpers."""
from __future__ import annotations

import unittest

from job_dashboard import _source_label


class TestSourceLabel(unittest.TestCase):
    def test_known_sources(self) -> None:
        self.assertEqual(_source_label("indeed"), "Indeed")
        self.assertEqual(_source_label("usajobs"), "USAJobs")

    def test_jobspy_prefix(self) -> None:
        self.assertEqual(_source_label("jobspy_glassdoor"), "Glassdoor (JobSpy)")

    def test_unknown_fallback(self) -> None:
        self.assertEqual(_source_label("custom_feed"), "Custom Feed")


if __name__ == "__main__":
    unittest.main()
