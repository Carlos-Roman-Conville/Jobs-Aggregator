"""
Canonical job pipeline lifecycle statuses (single source of truth).

Order (typical happy path):
  ingested → ranked → pending_review → drafted → approved → package_ready
  → submitted → responded | rejected
  closed = terminal without applying (skip, auto-filter, or withdraw from pipeline)
"""

from __future__ import annotations

from typing import FrozenSet

# Full set allowed in DB `job_pipeline_items.status`
CANONICAL_STATUSES: FrozenSet[str] = frozenset(
    {
        "ingested",  # Row created; not yet summarized/scored
        "ranked",  # Scores + summary_json written; immediately promoted to pending_review in practice
        "pending_review",  # Human queue
        "drafted",  # Needs edits / draft work before approval
        "approved",  # Human approved; ready to build package
        "package_ready",  # Package built; ready to apply
        "submitted",  # Application sent (your “applied”)
        "responded",  # Employer follow-up (interview/offer stage — optional use)
        "rejected",  # Employer rejection (optional use)
        "closed",  # Terminal: skipped, deferred-as-closed, auto-filtered junk, etc.
    }
)

# User-marked applied / post-application — never bulk-deleted by "clear all jobs"
COMPLETED_STATUSES: FrozenSet[str] = frozenset(
    {
        "submitted",
        "responded",
        "rejected",
    }
)

# Back-compat for API callers / old docs (normalized to canonical on read if needed)
LEGACY_STATUS_ALIASES = {
    "new": "ingested",
    "filtered": "closed",
    "skipped": "closed",
    "deferred": "closed",
    "needs_edits": "drafted",
    "applied": "submitted",
    "supervised_done": "submitted",
}
