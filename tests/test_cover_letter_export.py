"""Unit tests for cover_letter_export."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.cover_letter_export import (  # noqa: E402
    _build_rendercv_cover_yaml,
    assemble_cover_letter_markdown,
    export_cover_letter_markdown,
)


SAMPLE = {
    "opening": "I am applying for the IT Support role.",
    "body_paragraphs": ["I troubleshoot Linux and Windows endpoints."],
    "closing": "I welcome the opportunity to discuss my fit.",
}

PROFILE = {
    "name": "Test User",
    "contact": {"email": "test@example.com", "location": "Philadelphia, PA"},
}


class TestCoverLetterExport(unittest.TestCase):
    def test_markdown_includes_date_salutation_signoff(self):
        md = assemble_cover_letter_markdown(SAMPLE, company="Acme Corp", profile=PROFILE)
        self.assertIn("Hiring Team, Acme Corp", md)
        self.assertIn("Dear Hiring Team,", md)
        self.assertIn("Sincerely,", md)
        self.assertIn("Test User", md)
        self.assertIn("I am applying for the IT Support role.", md)

    def test_recipient_fallback_without_company(self):
        md = assemble_cover_letter_markdown(SAMPLE, company="", profile=PROFILE)
        self.assertIn("Hiring Team\n", md)
        self.assertNotIn("Hiring Team,\n\nDear", md)

    def test_paragraphs_separated_by_blank_lines(self):
        # New Editions Consulting leak: multi-paragraph bodies were joined with
        # single \n and rendered as one wall of text. Each prose block must be
        # separated by a blank line so the PDF shows real paragraph breaks.
        content = {
            "opening": "Opening line about role fit.",
            "body_paragraphs": [
                "Body paragraph 1 about prior experience.",
                "Body paragraph 2 about documentation habits.",
                "Body paragraph 3 about communication style.",
            ],
            "closing": "Closing line about next steps.",
        }
        md = assemble_cover_letter_markdown(content, company="Acme", profile=PROFILE)
        self.assertIn("Opening line about role fit.\n\nBody paragraph 1", md)
        self.assertIn("Body paragraph 1 about prior experience.\n\nBody paragraph 2", md)
        self.assertIn("Body paragraph 2 about documentation habits.\n\nBody paragraph 3", md)
        self.assertIn("Body paragraph 3 about communication style.\n\nClosing line", md)

    def test_yaml_emits_each_paragraph_as_separate_entry(self):
        # RenderCV's classic theme collapses a single TextEntry scalar with
        # embedded \n\n into one flowing block. Each paragraph must be its own
        # YAML list item so the PDF shows distinct paragraph breaks.
        content = {
            "opening": "Opening paragraph.",
            "body_paragraphs": [
                "Body paragraph one.",
                "Body paragraph two.",
            ],
            "closing": "Closing paragraph.",
        }
        md = assemble_cover_letter_markdown(content, company="Acme", profile=PROFILE)
        yaml_txt = _build_rendercv_cover_yaml(md, PROFILE)
        # Each prose block should appear as its own "      - ..." list entry,
        # not concatenated inside one entry with embedded \n\n.
        for needle in (
            "Opening paragraph.",
            "Body paragraph one.",
            "Body paragraph two.",
            "Closing paragraph.",
        ):
            self.assertIn(f'      - "{needle}"', yaml_txt, f"missing as own entry: {needle}")
        # No single entry should contain literal "\n\n" — that's the bug.
        for line in yaml_txt.splitlines():
            self.assertNotIn("\\n\\n", line, f"paragraph break left inside one entry: {line[:80]}")

    @patch("job_pipeline.cover_letter_export.load_consolidated_profile", return_value=PROFILE)
    def test_export_writes_file(self, _prof):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            path = export_cover_letter_markdown(
                SAMPLE,
                company="Acme",
                job_title="IT Support",
                item_id=7,
                outputs_root=td,
                profile=PROFILE,
            )
            self.assertTrue(Path(path).is_file())
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("tailored_7_Acme", path.replace("\\", "/"))
            self.assertIn("cover_letter.md", path.replace("\\", "/"))
            self.assertIn("Dear Hiring Team,", text)


if __name__ == "__main__":
    unittest.main()
