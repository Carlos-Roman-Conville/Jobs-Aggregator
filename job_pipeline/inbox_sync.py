"""IMAP ingestion of employer rejection mails → outcome updates."""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email import policy
from typing import Any, Dict, List

from job_pipeline.db import (
    list_pipeline_items_matching_submissions,
    set_item_outcome,
    update_item_status,
)

REJECTION_FRAGMENT = (
    "unfortunately",
    "will not be moving forward",
    "moving forward with other candidates",
    "not selected",
    "not move forward",
    "your application",
    "other candidates",
    "decided to pursue",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _match_row(email_blob: str, company: str, title: str) -> bool:
    ec = _norm(email_blob)
    if not ec:
        return False
    c = _norm(company)
    t = _norm(title)
    if len(c) > 5 and c in ec:
        return True
    for part in [p for p in re.split(r"[\|\-,/]", title) if len(p.strip()) > 10]:
        if _norm(part) in ec:
            return True
    if len(t) > 14 and (t[:20] in ec or t in ec):
        return True
    return False


def looks_like_rejection(subject: str, body_preview: str) -> bool:
    blob = _norm(subject) + "\n" + _norm(body_preview)
    strong = (
        "unfortunately",
        "not selected",
        "will not be moving forward",
        "not move forward",
    )
    if any(s in blob for s in strong):
        return True
    hits = sum(1 for phrase in REJECTION_FRAGMENT if phrase in blob)
    return hits >= 2


def sync_rejection_emails(
    *,
    dry_run: bool = True,
    max_messages: int = 120,
    lookback_hours: int = 240,
) -> Dict[str, Any]:
    """
    Simple rejection sync: scans INBOX-ish folder for mails that look rejections,
    attempts to correlate with submitted-but-not-recorded rejection rows.

    Requires environment:
      JOB_PIPELINE_IMAP_HOST, JOB_PIPELINE_IMAP_USER, JOB_PIPELINE_IMAP_PASSWORD
      optional JOB_PIPELINE_IMAP_FOLDER (default INBOX), JOB_PIPELINE_IMAP_TLS (true)
    """
    host = os.getenv("JOB_PIPELINE_IMAP_HOST") or ""
    user = os.getenv("JOB_PIPELINE_IMAP_USER") or ""
    passwd = os.getenv("JOB_PIPELINE_IMAP_PASSWORD") or ""
    folder = os.getenv("JOB_PIPELINE_IMAP_FOLDER") or "INBOX"
    use_tls = os.getenv("JOB_PIPELINE_IMAP_TLS", "true").lower() not in ("0", "false", "no")
    port = int(os.getenv("JOB_PIPELINE_IMAP_PORT") or ("993" if use_tls else "143"))

    if not host.strip() or not user.strip():
        return {
            "ok": False,
            "error": "missing_JOB_PIPELINE_IMAP_HOST_or_JOB_PIPELINE_IMAP_USER",
            "applied": [],
        }

    rows = list_pipeline_items_matching_submissions(limit=750)
    if not rows:
        return {"ok": True, "dry_run": dry_run, "applied": [], "skipped": [], "notice": "no_submitted_rows"}

    applied: List[Dict[str, Any]] = []
    skipped: List[str] = []

    if use_tls:
        M = imaplib.IMAP4_SSL(host.strip(), port)
    else:
        M = imaplib.IMAP4(host.strip(), port)
    try:
        typ, _dat = M.login(user.strip(), passwd)
        if typ != "OK":
            return {"ok": False, "error": "imap_login_failed"}
        typ, _ = M.select(folder)
        if typ != "OK":
            return {"ok": False, "error": f"imap_folder:{folder}_select_failed"}

        since = datetime.now(timezone.utc) - timedelta(hours=max(24, lookback_hours))
        # IMAP SEARCH SINCE DD-Mon-YYYY
        dd_mmm_yy = since.strftime("%d-%b-%Y")

        typ, data = M.search(None, f"SINCE {dd_mmm_yy}")
        ids = []
        if typ == "OK" and isinstance(data[0], (bytes, bytearray)):
            ids = data[0].split()
        examined = 0

        # newest first heuristic
        for mid in reversed(ids[-max_messages:]):
            if examined >= max_messages:
                break
            typ, msg_data = M.fetch(mid, "(RFC822)")
            if typ != "OK":
                skipped.append(str(mid))
                continue
            examined += 1
            raw_blob = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
            if not isinstance(raw_blob, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(bytes(raw_blob), policy=policy.default)
            subj = str(msg.get("subject") or "")
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain":
                        try:
                            body += part.get_content()
                        except Exception:
                            pass
            else:
                try:
                    body = msg.get_content()
                except Exception:
                    body = ""
            body_prev = (body or "")[:4000]
            if not looks_like_rejection(subj, body_prev):
                continue

            email_blob = subj + "\n" + body_prev
            for row in rows:
                if _match_row(email_blob, str(row.get("company_name") or ""), str(row.get("title") or "")):
                    item_id = int(row["item_id"])
                    if dry_run:
                        applied.append(
                            {
                                "item_id": item_id,
                                "company": row.get("company_name"),
                                "title": row.get("title"),
                                "dry_run_preview": True,
                            }
                        )
                    else:
                        update_item_status(item_id, "rejected", "imap:auto_rejection_mail")
                        set_item_outcome(item_id, "rejection", notes="matched_inbound_email_subject/body_heuristic")
                        applied.append({"item_id": item_id})
                    break

        return {"ok": True, "dry_run": dry_run, "applied": applied, "skipped": skipped}
    finally:
        try:
            M.logout()
        except Exception:
            pass
