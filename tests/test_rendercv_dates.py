"""RenderCV date normalization tests."""
import unittest

from job_pipeline.rendercv_export import normalize_rendercv_date


class TestNormalizeRendercvDate(unittest.TestCase):
    def test_month_year(self):
        self.assertEqual(normalize_rendercv_date("Sept 2024"), "2024-09")
        self.assertEqual(normalize_rendercv_date("Mar 2026"), "2026-03")

    def test_iso_passthrough(self):
        self.assertEqual(normalize_rendercv_date("2024-09"), "2024-09")
        self.assertEqual(normalize_rendercv_date("2011-10"), "2011-10")

    def test_present(self):
        self.assertEqual(normalize_rendercv_date("present"), "present")


if __name__ == "__main__":
    unittest.main()
