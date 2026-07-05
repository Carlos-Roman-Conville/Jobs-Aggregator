"""Unit tests for prompt framing version / stale detection (no DB)."""
from __future__ import annotations

import unittest

from job_pipeline.summarize import (
    PROMPT_FRAMING_VERSION,
    summary_prompt_framing_is_stale,
)


class TestPromptFramingStale(unittest.TestCase):
    def test_missing_version_is_stale(self) -> None:
        self.assertTrue(summary_prompt_framing_is_stale({}))
        self.assertTrue(summary_prompt_framing_is_stale({"verdict": "maybe"}))

    def test_current_version_not_stale(self) -> None:
        self.assertFalse(
            summary_prompt_framing_is_stale(
                {"prompt_framing_version": PROMPT_FRAMING_VERSION}
            )
        )

    def test_other_string_is_stale(self) -> None:
        self.assertTrue(summary_prompt_framing_is_stale({"prompt_framing_version": "v0"}))


if __name__ == "__main__":
    unittest.main()
