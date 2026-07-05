"""Tests for queue ordering helpers."""
from __future__ import annotations

import unittest

from job_pipeline.service import sort_items_recent_first


class TestSortItemsRecentFirst(unittest.TestCase):
    def test_pins_recent_ids_to_top(self):
        items = [
            {"item_id": 10, "title": "A"},
            {"item_id": 20, "title": "B"},
            {"item_id": 30, "title": "C"},
        ]
        out = sort_items_recent_first(items, [30, 10])
        self.assertEqual([r["item_id"] for r in out], [30, 10, 20])

    def test_no_recent_ids_returns_original_order(self):
        items = [{"item_id": 1}, {"item_id": 2}]
        self.assertIs(sort_items_recent_first(items, []), items)


if __name__ == "__main__":
    unittest.main()
