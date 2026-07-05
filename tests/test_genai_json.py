"""Tests for genai_json parsing helpers."""
from __future__ import annotations

import json
import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.genai_json import parse_json_object_from_model  # noqa: E402


class TestParseJsonObjectFromModel(unittest.TestCase):
    def test_plain_json(self):
        obj = parse_json_object_from_model('{"summary": "hello", "experience": []}')
        self.assertEqual(obj["summary"], "hello")

    def test_markdown_fence(self):
        raw = 'Here you go:\n```json\n{"summary": "x", "skills": {"technical": [], "soft": []}}\n```'
        obj = parse_json_object_from_model(raw)
        self.assertEqual(obj["summary"], "x")

    def test_prose_before_object(self):
        raw = 'Sure! {"summary": "y", "experience": [{"title": "A", "company": "B", "duration": "1y", "bullets": []}]}'
        obj = parse_json_object_from_model(raw)
        self.assertEqual(obj["summary"], "y")

    def test_nested_strings_with_braces(self):
        payload = {"summary": "Uses {tools} daily", "experience": []}
        raw = json.dumps(payload)
        obj = parse_json_object_from_model(raw)
        self.assertIn("{tools}", obj["summary"])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_json_object_from_model("   ")


if __name__ == "__main__":
    unittest.main()
