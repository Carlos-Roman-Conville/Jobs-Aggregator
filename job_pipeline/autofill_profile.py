"""Normalize consolidated_profile.json into a browser-autofill payload."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.bootstrap_resume_profile import load_consolidated_profile

_MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def default_autofill_json_path() -> Path:
    return _repo_root() / "autofill_profile.json"


def default_references_json_path() -> Path:
    return _repo_root() / "references.json"


def default_screening_answers_json_path() -> Path:
    return _repo_root() / "screening_answers.json"


def default_ats_account_json_path() -> Path:
    return _repo_root() / "ats_account.json"


def default_ats_credentials_json_path() -> Path:
    return _repo_root() / "ats_credentials.json"


def load_ats_account() -> Dict[str, Any]:
    """Load shared ATS account-creation data (password + security Q&A)."""
    path = default_ats_account_json_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def load_references() -> List[Dict[str, Any]]:
    """Load professional references from job_pipeline/references.json."""
    path = default_references_json_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    refs = raw.get("references") if isinstance(raw, dict) else raw
    if isinstance(refs, list):
        return [r for r in refs if isinstance(r, dict)]
    return []


def load_screening_answers() -> Dict[str, Any]:
    """Load standing screening answers from job_pipeline/screening_answers.json."""
    path = default_screening_answers_json_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def _split_name(full: str) -> Dict[str, str]:
    raw = re.sub(r"\s+", " ", (full or "").strip())
    if not raw:
        return {"full_name": "", "first_name": "", "middle_name": "", "last_name": ""}

    parts = raw.split()
    if len(parts) == 1:
        return {"full_name": raw, "first_name": parts[0], "middle_name": "", "last_name": ""}

    first = parts[0].title()
    last_part = parts[-1]
    if "-" in last_part:
        last = last_part.title()
    else:
        last = last_part.title()

    middle = ""
    if len(parts) > 2:
        mid = parts[1:-1]
        if len(mid) == 1 and len(mid[0]) <= 2 and mid[0].endswith("."):
            middle = mid[0]
        else:
            middle = " ".join(mid)

    display = f"{first} {middle + ' ' if middle else ''}{last}".strip()
    return {
        "full_name": display,
        "first_name": first,
        "middle_name": middle,
        "last_name": last,
    }


def _parse_ym(value: str) -> Tuple[str, str, str, str]:
    """Return month_num, year, mm/yyyy, Month YYYY from YYYY-MM or loose strings."""
    text = (value or "").strip()
    if not text or text.lower() in ("present", "current", "now"):
        return "", "", "", "Present"

    m = re.match(r"^(\d{4})[-/](\d{1,2})", text)
    if m:
        year, month = m.group(1), int(m.group(2))
        mm = f"{month:02d}"
        month_name = _MONTH_NAMES[month] if 1 <= month <= 12 else mm
        return mm, year, f"{mm}/{year}", f"{month_name} {year}"

    m2 = re.match(r"^(\d{4})$", text)
    if m2:
        year = m2.group(1)
        return "", year, year, year

    return "", "", text, text


def _parse_location(loc: str) -> Dict[str, str]:
    text = (loc or "").strip()
    city, state, country = "", "", "United States"
    if not text:
        return {"city": city, "state": state, "country": country, "full": ""}

    if "," in text:
        bits = [b.strip() for b in text.split(",") if b.strip()]
        if len(bits) >= 2:
            city, state = bits[0], bits[1]
        elif bits:
            city = bits[0]
    else:
        city = text

    return {"city": city, "state": state, "country": country, "full": text}


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _bullets_to_description(bullets: Any, *, max_bullets: int = 6) -> str:
    if not isinstance(bullets, list):
        return ""
    lines = [str(b).strip() for b in bullets if str(b).strip()]
    return "\n".join(f"• {line}" if not line.startswith("•") else line for line in lines[:max_bullets])


def _canonical_title_for(company: str, fallback: str) -> str:
    """Override the resume/profile title with evidence.json title_canonical when matched.

    This keeps the autofill form and the tailored résumé PDF using the same titles,
    which prevents the recruiter-visible mismatch between uploaded PDF and typed
    form fields. evidence.json is the single source of truth.
    """
    try:
        from job_pipeline.evidence_db import employer_record, match_employer_key

        key = match_employer_key(company or "")
        if not key:
            return (fallback or "").strip()
        rec = employer_record(key) or {}
        canonical = str(rec.get("title_canonical") or "").strip()
        return canonical or (fallback or "").strip()
    except Exception:
        return (fallback or "").strip()


def _normalize_experience(entries: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for row in entries:
        if not isinstance(row, dict):
            continue
        sm, sy, s_slash, s_disp = _parse_ym(str(row.get("start_date") or ""))
        em, ey, e_slash, e_disp = _parse_ym(str(row.get("end_date") or ""))
        loc = _parse_location(str(row.get("location") or ""))
        bullets = row.get("bullets") or []
        company_str = str(row.get("company") or "").strip()
        out.append(
            {
                "company": company_str,
                "title": _canonical_title_for(company_str, str(row.get("title") or "")),
                "location": loc["full"],
                "city": loc["city"],
                "state": loc["state"],
                "start_date": str(row.get("start_date") or "").strip(),
                "end_date": str(row.get("end_date") or "").strip(),
                "start_month": sm,
                "start_year": sy,
                "end_month": em,
                "end_year": ey,
                "start_slash": s_slash,
                "end_slash": e_slash,
                "start_display": s_disp,
                "end_display": e_disp,
                "currently_work_here": not str(row.get("end_date") or "").strip()
                or str(row.get("end_date") or "").lower() in ("present", "current"),
                "description": _bullets_to_description(bullets),
            }
        )
    return out


def _normalize_education(entries: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for row in entries:
        if not isinstance(row, dict):
            continue
        loc = _parse_location(str(row.get("location") or ""))
        _, grad_year, _, grad_disp = _parse_ym(str(row.get("end_date") or ""))
        out.append(
            {
                "school": str(row.get("school") or row.get("institution") or "").strip(),
                "degree": str(row.get("degree") or row.get("area") or "").strip(),
                "field_of_study": str(row.get("field_of_study") or row.get("area") or "").strip(),
                "location": loc["full"],
                "graduation_year": grad_year,
                "graduation_display": grad_disp,
                "details": str(row.get("details") or "").strip(),
                "gpa": str(row.get("gpa") or "").strip(),
            }
        )
    return out


def _normalize_military(entries: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for row in entries:
        if not isinstance(row, dict):
            continue
        sm, sy, _, s_disp = _parse_ym(str(row.get("start_date") or ""))
        em, ey, _, e_disp = _parse_ym(str(row.get("end_date") or ""))
        branch_str = str(row.get("branch") or "").strip()
        role_fallback = str(row.get("role") or row.get("title") or "").strip()
        out.append(
            {
                "branch": branch_str,
                "role": _canonical_title_for(branch_str, role_fallback),
                "rank": str(row.get("rank") or "").strip(),
                "start_month": sm,
                "start_year": sy,
                "end_month": em,
                "end_year": ey,
                "start_display": s_disp,
                "end_display": e_disp,
                "description": _bullets_to_description(row.get("bullets") or [], max_bullets=4),
            }
        )
    return out


def _normalize_references(entries: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for row in entries:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        name_bits = _split_name(name) if name else {"full_name": "", "first_name": "", "last_name": ""}
        phone = str(row.get("phone") or "").strip()
        out.append(
            {
                "name": name_bits["full_name"] or name,
                "first_name": str(row.get("first_name") or name_bits["first_name"] or "").strip(),
                "last_name": str(row.get("last_name") or name_bits["last_name"] or "").strip(),
                "title": str(row.get("title") or row.get("job_title") or "").strip(),
                "company": str(row.get("company") or row.get("organization") or "").strip(),
                "relationship": str(row.get("relationship") or row.get("relation") or "").strip(),
                "email": str(row.get("email") or "").strip(),
                "phone": phone,
                "phone_digits": _phone_digits(phone),
            }
        )
    return out


def build_autofill_profile(profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build extension-ready autofill JSON from consolidated profile."""
    src = profile if isinstance(profile, dict) else load_consolidated_profile()
    contact_raw = src.get("contact") if isinstance(src.get("contact"), dict) else {}
    name_bits = _split_name(str(src.get("name") or ""))
    loc = _parse_location(str(contact_raw.get("location") or ""))
    phone = str(contact_raw.get("phone") or "").strip()

    refs_raw = src.get("references")
    if isinstance(refs_raw, list) and refs_raw:
        references = _normalize_references(refs_raw)
    else:
        references = _normalize_references(load_references())

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contact": {
            **name_bits,
            "email": str(contact_raw.get("email") or "").strip(),
            "phone": phone,
            "phone_digits": _phone_digits(phone),
            "location": loc["full"],
            "street_address": str(contact_raw.get("street_address") or "").strip(),
            "city": loc["city"],
            "state": loc["state"],
            "postal_code": str(contact_raw.get("postal_code") or contact_raw.get("zip") or contact_raw.get("zipcode") or "").strip(),
            "country": loc["country"] or "United States",
            "linkedin": str(contact_raw.get("linkedin") or "").strip(),
            "github": str(contact_raw.get("github") or "").strip(),
            "website": str(contact_raw.get("website") or "").strip(),
        },
        "summary": str(src.get("summary") or src.get("headline") or "").strip(),
        "experience": _normalize_experience(src.get("experience")),
        "education": _normalize_education(src.get("education")),
        "military": _normalize_military(src.get("military_service")),
        "references": references,
        "screening": load_screening_answers(),
        "ats_account": load_ats_account(),
    }


def write_autofill_profile_json(path: Optional[Path] = None) -> Path:
    out_path = path or default_autofill_json_path()
    payload = build_autofill_profile()
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
