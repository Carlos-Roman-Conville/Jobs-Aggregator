"""Clear-all jobs must preserve completed (submitted/responded/rejected) rows."""
from __future__ import annotations

import unittest
from unittest import mock

from job_pipeline import db
from job_pipeline.states import COMPLETED_STATUSES


class TestClearPreservesCompleted(unittest.TestCase):
    @mock.patch.object(db, "pg_connect")
    def test_clear_deletes_only_non_completed(self, mock_connect) -> None:
        cur = mock.MagicMock()
        conn = mock.MagicMock()
        mock_connect.return_value = conn
        conn.cursor.return_value.__enter__ = mock.Mock(return_value=cur)
        conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        cur.fetchone.side_effect = [
            (2,),  # items_preserved
            (5,),  # items_deleted
            (10,),  # postings_before
            (3,),  # postings_preserved after delete
        ]
        cur.rowcount = 7

        result = db.clear_all_pipeline_jobs()

        self.assertEqual(result["items_preserved"], 2)
        self.assertEqual(result["items_deleted"], 5)
        delete_calls = [c for c in cur.execute.call_args_list if "DELETE" in str(c[0][0])]
        self.assertGreaterEqual(len(delete_calls), 2)
        completed = sorted(COMPLETED_STATUSES)
        self.assertEqual(delete_calls[0][0][1], (completed,))

    def test_completed_statuses_include_submitted(self) -> None:
        self.assertIn("submitted", COMPLETED_STATUSES)


if __name__ == "__main__":
    unittest.main()
