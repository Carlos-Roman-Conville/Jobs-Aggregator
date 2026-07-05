"""
Lane categorization for the multi-agent apply workflow.

Assigns every posting to exactly ONE category so each Claude Code agent can be
pointed at a dashboard tab and pull `WHERE category = '<id>'` without relying on
SQL row-locking / claim columns (which drifted when agents worked off stale
candidate lists).

Four user-facing tabs (precedence, highest first):

    IT Help Desk   -> it_helpdesk    : Tier 1 helpdesk / service desk / desktop / IT support
    IT General     -> it_general     : sysadmin, NOC, network, systems, IT specialist,
                                       cyber/infosec, cloud/DBA, software/dev, data
    Operations     -> operations     : ops manager/coordinator, service manager,
                                       implementation, account mgr, onboarding, CS
    Remote         -> remote_non_it  : REMOTE roles that are NOT IT and NOT operations
                                       (remote customer support/service, general remote)

Anything non-IT, non-ops, and not clearly remote falls to `other` (hidden tab).

Classification is on the TITLE (deterministic, precise) rather than the shared
`classify_role_family`, which scans the JD body and bleeds (customer-support roles
matched "support", federal "IT Specialist" titles matched nothing). A boundary
title like "IT Operations Support Specialist" resolves to ONE tab (IT General,
since IT outranks Operations) instead of being grabbed by two agents.
"""

from __future__ import annotations

import re

from job_pipeline.location_policy import classify_remote_hybrid_on_site

# Category ids (stored in job_pipeline_items.category)
CAT_IT_HELPDESK = "it_helpdesk"
CAT_IT_GENERAL = "it_general"
CAT_OPERATIONS = "operations"
CAT_REMOTE_NON_IT = "remote_non_it"
CAT_OTHER = "other"

# Human labels for the dashboard tabs
CATEGORY_LABELS = {
    CAT_IT_HELPDESK: "IT Help Desk",
    CAT_IT_GENERAL: "IT General",
    CAT_OPERATIONS: "Operations",
    CAT_REMOTE_NON_IT: "Remote",
    CAT_OTHER: "Other",
}

# Tab display order (Other intentionally last)
CATEGORY_ORDER = [
    CAT_IT_HELPDESK,
    CAT_IT_GENERAL,
    CAT_OPERATIONS,
    CAT_REMOTE_NON_IT,
    CAT_OTHER,
]

# --- Title rules (checked in precedence order) ---

# Tier 1 helpdesk / deskside. Requires a genuine IT-helpdesk term, NOT a bare
# "support specialist" (which catches benefits/customer support, etc.).
_HELPDESK_RE = re.compile(
    r"help\s?desk|service desk|desktop support|deskside|end[\s-]?user support"
    r"|\bit support\b|technical support|support technician|computer support"
    r"|1st line support|2nd line support|\btier\s?(?:1|2|i|ii)\b"
    r"|computer user support|\buser support\b",
    re.I,
)

# Broader IT / technical roles that aren't helpdesk.
_IT_GENERAL_RE = re.compile(
    r"\b(?:it|information technology)\s+(?:support|specialist|technician|admin|"
    r"administrator|analyst|manager|operations|services?|security|infrastructure|help)"
    r"|information technology"
    r"|sys\s?admin|systems? admin|systems? administrator"
    r"|network (?:admin|administrator|engineer|specialist|technician)|\bnoc\b"
    r"|infrastructure|systems? analyst|systems? specialist|systems? engineer"
    r"|devops|\bcloud\b|\bdatabase\b|\bdba\b|cyber\s?security|\bcyber\b|infosec"
    r"|information security|software (?:engineer|developer)|\bdeveloper\b"
    r"|data (?:engineer|analyst)|computer systems"
    r"|systems? technician|computer technician|\bpc technician\b"
    r"|technical consultant|incident manager",
    re.I,
)

# Operations / coordination / customer success.
_OPERATIONS_RE = re.compile(
    r"\boperations\b|\bops\b|coordinator|service manager|implementation"
    r"|account manager|onboarding|customer success|client success|office manager"
    r"|\bdispatch\b|logistics|scheduler|program manager|service delivery"
    r"|process improvement|business operations",
    re.I,
)

# Location field that plainly states remote even if the JD body is vague.
_REMOTE_LOC_RE = re.compile(r"\bremote\b|anywhere", re.I)


def classify_category(title: str, desc: str = "", location: str = "") -> str:
    """Return exactly one category id for a posting.

    Precedence: IT Help Desk -> IT General -> Operations -> Remote(non-IT) -> Other.
    """
    t = title or ""

    if _HELPDESK_RE.search(t):
        return CAT_IT_HELPDESK
    if _IT_GENERAL_RE.search(t):
        return CAT_IT_GENERAL
    if _OPERATIONS_RE.search(t):
        return CAT_OPERATIONS

    # Not IT, not operations. Bucket remote roles into the Remote tab; everything
    # else (onsite/hybrid/unknown non-IT) is Other and stays out of the main tabs.
    mode = classify_remote_hybrid_on_site(t, location or "", desc or "")
    if mode == "remote" or _REMOTE_LOC_RE.search(location or ""):
        return CAT_REMOTE_NON_IT
    return CAT_OTHER


def category_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat or "", cat or "Other")
