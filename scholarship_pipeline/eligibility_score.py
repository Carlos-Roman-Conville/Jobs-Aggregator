"""
Score each scholarship against Carlos's profile.

Eligibility fit (0-1) is LLM-derived from the scholarship's eligibility_criteria
+ description + provider, matched against Carlos's profile facts.

Priority score = eligibility_fit * deadline_urgency
where deadline_urgency = exp(-days_to_deadline / 30) for fixed deadlines, or
0.5 for rolling deadlines.

For the MVP we score deterministically (rule-based) — no LLM call yet — so the
ranking works immediately. A later iteration can swap in LLM-based scoring
using the same shape (input dict, output (fit, notes)).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from scholarship_pipeline.db import (
    list_queue,
    pg_connect,
    set_item_scoring,
)

# Carlos's profile facts, encoded as scoring inputs.
CARLOS_PROFILE = {
    "is_veteran": True,             # Army Reserve 2011-2020, honorable
    "is_active_duty": False,
    "is_reservist": True,
    "is_protected_veteran": False,  # no VA disability, no combat badge, etc.
    "is_hispanic": True,            # PR-born
    "state": "PA",                  # Pennsylvania resident
    "is_us_citizen_or_pr": True,
    "degree_level": "undergraduate",  # WGU BS in progress
    "field_of_study": "cybersecurity",
    "gpa": 3.80,
    "is_adult_learner": True,       # career-changer, 30+
    "completed_degrees": ["BA Political Science (Rowan, Cum Laude 3.80)"],
    "is_second_bachelors": True,    # WGU BS Cybersec is his SECOND bachelor's.
                                    # Disqualifies: Pell Grant, Federal Subsidized
                                    # loans, most "first-time freshman" scholarships,
                                    # and likely PHEAA PA State Grant (8-semester cap).
                                    # Most external private scholarships still OK.
    "school": "WGU",                # admitted, transcripts sent
    "school_accredited": True,
}


def _days_until(deadline_at: Optional[datetime]) -> Optional[int]:
    if deadline_at is None:
        return None
    if deadline_at.tzinfo is None:
        deadline_at = deadline_at.replace(tzinfo=timezone.utc)
    delta = deadline_at - datetime.now(timezone.utc)
    return int(delta.total_seconds() / 86400)


def _deadline_urgency(deadline_at: Optional[datetime], rolling: bool) -> float:
    """Higher = more urgent. Uses a gentler decay so high-fit scholarships
    with deadlines 6-12 months out still surface above rolling-deadline ones.

    Curve (1 / (1 + days/180)):
        today:    1.00
        30 days:  0.857
        90 days:  0.667
        180 days: 0.500
        365 days: 0.330
        730 days: 0.198
    Past deadlines collapse to 0 (skip).
    """
    if rolling:
        return 0.4  # rolling is "always sort-of urgent but never the most urgent"
    days = _days_until(deadline_at)
    if days is None:
        return 0.25  # unknown deadline, low confidence
    if days < 0:
        return 0.0  # already past — skip
    return 1.0 / (1.0 + days / 180.0)


def _string_contains_any(text: Optional[str], needles: List[str]) -> bool:
    if not text:
        return False
    s = text.lower()
    return any(n.lower() in s for n in needles)


def score_one(row: Dict[str, Any]) -> Tuple[float, str]:
    """Return (eligibility_fit_score in [0,1], notes).
    Rule-based for MVP — replace with LLM-based scoring later if needed."""
    fit = 0.5  # baseline (a generic open undergraduate scholarship)
    notes: List[str] = []

    elig = row.get("eligibility_criteria") or ""
    title = row.get("title") or ""
    desc = row.get("description_text") or ""
    field = (row.get("field_of_study") or "").lower()
    geo = (row.get("geographic_restriction") or "").lower()
    degree = (row.get("degree_level") or "").lower()
    blob = " ".join([title, desc, elig]).lower()

    # ---- Hard exclusions (lower fit aggressively) ------------------------
    if degree == "graduate" and CARLOS_PROFILE["degree_level"] != "graduate":
        fit -= 0.25
        notes.append("graduate-only: Carlos is BS, downweight unless verifies BS-eligible")

    if "women" in blob and "women in cyber" in blob:
        # Many "Women in Cybersecurity" scholarships explicitly require female applicants
        fit -= 0.5
        notes.append("women-only scholarship — Carlos male, exclude")

    if "national guard" in blob and "reservist" not in blob and "reserve" not in blob:
        fit -= 0.2
        notes.append("Guard-only — Carlos is Reserve, downweight")

    if "active duty" in blob and "reserv" not in blob and "veteran" not in blob:
        fit -= 0.25
        notes.append("active-duty only — Carlos was Reserve, downweight")

    # Second-bachelor's exclusions.
    if CARLOS_PROFILE.get("is_second_bachelors") and _string_contains_any(blob, [
        "first-time freshman", "first time freshman", "first bachelor", "first-time college",
        "entering freshman", "incoming freshman", "no prior degree", "first undergraduate degree",
    ]):
        fit -= 0.5
        notes.append("first-time-undergrad-only — Carlos has prior BA from Rowan, exclude")

    if CARLOS_PROFILE.get("is_second_bachelors") and _string_contains_any(blob, [
        "pell grant required", "pell-eligible required", "pell recipient",
    ]):
        fit -= 0.4
        notes.append("requires Pell eligibility — Carlos has prior BA, ineligible for Pell")

    if geo and geo not in ("any", "us", "us / any", "united states"):
        # Geographic restriction — match against PA only
        if "pa" not in geo and "pennsylvania" not in geo:
            fit -= 0.3
            notes.append(f"geographic restriction: {geo!r} excludes PA")
        else:
            fit += 0.15
            notes.append("PA-specific scholarship — strong fit")

    # ---- Positive matches -------------------------------------------------
    if CARLOS_PROFILE["is_veteran"] and _string_contains_any(blob, [
        "veteran", "military", "service member", "reservist", "reserve"
    ]):
        fit += 0.2
        notes.append("vet/military signal matched")

    if CARLOS_PROFILE["is_hispanic"] and _string_contains_any(blob, [
        "hispanic", "latino", "latinx", "latina", "hsf",
    ]):
        fit += 0.2
        notes.append("Hispanic signal matched")

    if CARLOS_PROFILE["field_of_study"] == "cybersecurity" and _string_contains_any(blob, [
        "cyber", "cybersecurity", "information security", "infosec", "cyber-security",
    ]):
        fit += 0.15
        notes.append("cybersecurity field match")

    if "stem" in blob and CARLOS_PROFILE["field_of_study"] == "cybersecurity":
        fit += 0.05
        notes.append("STEM umbrella")

    if "wgu" in blob or "western governors" in blob:
        fit += 0.15
        notes.append("WGU-specific — Carlos is enrolled")

    if "adult" in blob or "non-traditional" in blob or "career-changer" in blob:
        fit += 0.05
        notes.append("adult-learner / non-trad signal")

    # ---- GPA gates --------------------------------------------------------
    min_gpa = row.get("min_gpa")
    if min_gpa:
        try:
            if float(CARLOS_PROFILE["gpa"]) >= float(min_gpa):
                fit += 0.05
                notes.append(f"GPA gate {min_gpa} cleared (Carlos {CARLOS_PROFILE['gpa']})")
            else:
                fit -= 0.4
                notes.append(f"GPA gate {min_gpa} FAILED (Carlos {CARLOS_PROFILE['gpa']})")
        except Exception:
            pass

    # ---- Verification flag ------------------------------------------------
    if "needs_verification" in blob.lower():
        notes.append("needs_verification — confirm details with sponsor before applying")

    # Clamp
    fit = max(0.0, min(1.0, fit))
    return fit, " | ".join(notes)


def score_all(limit: int = 200) -> List[Dict[str, Any]]:
    """Score every ingested scholarship + persist. Returns the scored rows."""
    rows = list_queue(status="ingested", limit=limit, order_by="i.id ASC")
    # If nothing ingested, try the broader pool of un-scored rows
    if not rows:
        rows = list_queue(limit=limit, order_by="i.id ASC")

    out: List[Dict[str, Any]] = []
    for row in rows:
        # Need the full row (with description_text + eligibility_criteria), so re-fetch
        full = _full_row(row["id"])
        fit, notes = score_one(full)
        urg = _deadline_urgency(full.get("deadline_at"), bool(full.get("rolling_deadline")))
        priority = fit * urg
        set_item_scoring(
            item_id=row["id"],
            eligibility_fit_score=fit,
            deadline_urgency=urg,
            priority_score=priority,
            eligibility_notes=notes,
            new_status="ranked",
        )
        out.append({
            "id": row["id"],
            "title": full.get("title"),
            "fit": round(fit, 3),
            "urgency": round(urg, 3),
            "priority": round(priority, 3),
            "deadline_at": full.get("deadline_at"),
            "notes": notes,
        })
    return out


def _full_row(item_id: int) -> Dict[str, Any]:
    """Need full posting fields for scoring — list_queue doesn't return all."""
    from scholarship_pipeline.db import get_item
    return get_item(item_id) or {}


if __name__ == "__main__":
    rows = score_all()
    rows.sort(key=lambda r: r["priority"], reverse=True)
    print(f"Scored {len(rows)} scholarships. Top 10 by priority:\n")
    for r in rows[:10]:
        dl = r["deadline_at"]
        print(f"  fit={r['fit']:.2f}  urg={r['urgency']:.2f}  priority={r['priority']:.3f}  | {r['title'][:60]}")
        print(f"     deadline: {dl}")
        print(f"     notes: {r['notes'][:160]}")
        print()
