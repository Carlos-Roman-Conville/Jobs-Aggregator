"""Location / remote policy from job_pipeline_config merged dict."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from job_pipeline.domain_fit import posting_has_tech_role_signal


def location_policy_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    f = cfg.get("filters") if isinstance(cfg.get("filters"), dict) else {}
    loc = f.get("location_policy") if isinstance(f.get("location_policy"), dict) else {}
    if not loc.get("enabled", False):
        return {"enabled": False}
    metro: List[str] = []
    raw_metro = loc.get("metro_keywords") or loc.get("home_metro_keywords")
    if isinstance(raw_metro, list):
        metro = [str(x).strip().lower() for x in raw_metro if str(x).strip()]
    return {
        "enabled": True,
        "metro_keywords": metro or ["philadelphia", "philly", "greater philadelphia"],
        "reject_non_remote_outside_metro": bool(loc.get("reject_non_remote_outside_metro", True)),
        "require_tech_role_for_onsite": bool(loc.get("require_tech_role_for_onsite", True)),
        "metro_mult": float(loc.get("metro_bonus_mult", 1.06)),
        "remote_mult": float(loc.get("remote_bonus_mult", 1.08)),
    }


# Work-mode phrases that ONLY describe the job's location model — never used
# as duty descriptions. "remote" by itself is intentionally NOT in here because
# JD bullets like "support remote users" / "remote troubleshooting" use the
# word in a duty sense and would otherwise vote the whole posting as Remote.
_REMOTE_WORK_MODE_PHRASES = frozenset(
    {
        "fully remote",
        "100% remote",
        "100 percent remote",
        "national remote",
        "us remote",
        "united states remote",
        "anywhere in the us",
        "anywhere in the united states",
        "work from home",
        "work-from-home",
        "wfh",
        "remote position",
        "remote role",
        "remote opportunity",
        "remote-first",
        "remote first",
        "this role is remote",
        "this position is remote",
    }
)
_ONSITE_HINTS = frozenset({"on-site", "onsite", "in office", "in-office", "on site"})
_HYBRID_HINTS = frozenset({"hybrid", "days per week", "office days", "days in office", "days a week in"})

# Declarative "Location:" / "Job Location:" / "Where you'll work:" patterns in JD body.
_DECLARATIVE_LOCATION_RE = re.compile(
    r"\b(?:Location|Job\s+Location|Office\s+Location|Where\s+you[''](?:re|ll)\s+work|Work\s+Location)\s*:\s*([^\n]+?)(?:\n|$)",
    re.IGNORECASE,
)

# "Hybrid in <city>" / "Hybrid - <city>" / "Hybrid out of <city>" — strong
# work-mode signal anywhere in the body, not just declarative line.
_HYBRID_BODY_PATTERN_RE = re.compile(
    r"\bhybrid\s+(?:in|at|near|out\s+of|from|-+\s*|–\s*)\s*[A-Z][\w.,\s]{2,40}",
    re.IGNORECASE,
)
# "Onsite in <city>" similar.
_ONSITE_BODY_PATTERN_RE = re.compile(
    r"\b(?:on[-\s]?site|in[-\s]?office)\s+(?:in|at|near|-+\s*|–\s*)\s*[A-Z][\w.,\s]{2,40}",
    re.IGNORECASE,
)


def _blob(title: str, location: str, desc: str) -> str:
    return f"{title or ''}\n{location or ''}\n{desc or ''}".lower()


def _parse_declarative_mode(loc_line: str) -> str:
    """Parse a 'Location: …' line into remote / hybrid / onsite / unknown.

    Hybrid wins over Remote when BOTH appear (e.g. "Hybrid (Philadelphia, PA;
    remote 2 days)" is hybrid, not remote).
    """
    s = (loc_line or "").lower().strip()
    if not s:
        return "unknown"
    has_hybrid = bool(re.search(r"\bhybrid\b", s))
    has_onsite = bool(re.search(r"\b(?:on[-\s]?site|onsite|in[-\s]?office)\b", s))
    has_remote = bool(re.search(r"\b(?:remote|work\s*from\s*home|wfh)\b", s))
    if has_hybrid:
        return "hybrid"
    if has_onsite and not has_remote:
        return "onsite"
    if has_remote and not has_onsite:
        return "remote"
    if has_onsite:
        return "onsite"
    return "unknown"


def classify_remote_hybrid_on_site(title: str, location: str, desc: str) -> str:
    """Return coarse label: remote | hybrid | onsite | unknown.

    Precedence (highest first):
      1. Declarative "Location: …" line in the JD body
      2. "Hybrid in <city>" / "Onsite in <city>" body patterns
      3. Federal-style "telework eligible" markers
      4. Counted phrase votes (filtered to work-mode-only phrases)
      5. The posting.location field as last resort
    """
    desc_full = desc or ""
    b = _blob(title, location, desc)

    # 1. Declarative line wins — JD authors who write "Location: Hybrid in
    # Deerfield Beach, FL" mean it.
    m = _DECLARATIVE_LOCATION_RE.search(desc_full[:6000])
    if m:
        mode = _parse_declarative_mode(m.group(1))
        if mode != "unknown":
            return mode

    # 2. "Hybrid in <Capitalized>" pattern — strong work-mode signal even
    # without a declarative header.
    if _HYBRID_BODY_PATTERN_RE.search(desc_full[:8000]):
        return "hybrid"
    if _ONSITE_BODY_PATTERN_RE.search(desc_full[:8000]):
        return "onsite"

    # 3. Federal / USAJOBS-style telework signals.
    if re.search(r"\btelework\s+(?:eligible|authorized)\b", b):
        return "hybrid"
    if re.search(r"\bremote\s+work\s+(?:eligible|authorized)\b", b):
        return "remote"

    # 4. Vote-count with WORK-MODE-ONLY phrases (bare "remote" excluded).
    r = sum(1 for p in _REMOTE_WORK_MODE_PHRASES if p in b)
    h = sum(1 for p in _HYBRID_HINTS if p in b)
    o = sum(1 for p in _ONSITE_HINTS if p in b)
    if h >= 1 and h >= max(r, o):
        return "hybrid"
    if r >= 1 and r >= o:
        return "remote"
    if o >= 1:
        return "onsite"

    # 5. Posting.location field as last resort — only when no body signal.
    loc_low = (location or "").lower()
    if re.search(r"\bhybrid\b", loc_low):
        return "hybrid"
    if re.search(r"\b(?:on[-\s]?site|onsite|in[-\s]?office)\b", loc_low):
        return "onsite"
    if re.search(r"\bremote\b", loc_low):
        # Bare "remote" in posting.location field is suspect (Indeed et al.
        # often label hybrid jobs as remote). Require it to be the dominant
        # signal in the location field, not just a substring.
        if re.search(r"\b(?:hybrid|onsite|in[-\s]?office)\b", loc_low):
            return "hybrid" if "hybrid" in loc_low else "onsite"
        return "remote"
    return "unknown"


def _mentions_metro(blob_low: str, metro_keywords: List[str]) -> bool:
    """True if the blob references the home metro.

    Short keywords (state abbreviations like ``pa``, ``ny``) MUST match as whole
    tokens — a bare substring check matched ``pa`` inside "partners",
    "department", "separate" etc., which made virtually every onsite/hybrid job
    in the country read as "Philadelphia-metro" and get accepted instead of
    rejected. Longer keywords ("philadelphia") are safe as substrings.
    """
    for kw in metro_keywords:
        if not kw:
            continue
        if len(kw) <= 3:
            if re.search(r"\b" + re.escape(kw) + r"\b", blob_low):
                return True
        elif kw in blob_low:
            return True
    return False


def evaluate_location_policy(
    title: str,
    location: str,
    desc: str,
    cfg: Dict[str, Any],
) -> Tuple[str, float, str, str]:
    """
    Returns:
      action: accept | neutral | reject
      multiplier applied to blended fit (neutral 1.0)
      classification: remote|hybrid|onsite|unknown
      reason_code (may be "")
    """
    loc_cfg = location_policy_settings(cfg)
    if not loc_cfg.get("enabled"):
        return "accept", 1.0, classify_remote_hybrid_on_site(title, location, desc), ""

    cls = classify_remote_hybrid_on_site(title, location, desc)
    blob_low = _blob(title, location, desc).lower()
    metro = loc_cfg.get("metro_keywords") or []

    reject_outside = bool(loc_cfg.get("reject_non_remote_outside_metro"))
    need_tech_onsite = bool(loc_cfg.get("require_tech_role_for_onsite"))
    metro_m = float(loc_cfg.get("metro_mult") or 1.06)
    rem_m = float(loc_cfg.get("remote_mult") or 1.08)

    def tech_ok() -> bool:
        if not need_tech_onsite:
            return True
        return posting_has_tech_role_signal(title, desc)

    if cls == "remote":
        return "accept", round(rem_m, 4), cls, ""

    if cls == "unknown":
        if "remote eligible" in blob_low or re.search(r"\bremote\b", blob_low):
            return "neutral", round((1.0 + rem_m) / 2.0, 4), cls, "assume_remote_signals"
        # Unknown work-mode = we genuinely could not determine remote/onsite from
        # the (often thin) stored text. Per the high-recall policy (score gate is
        # 0.0; Carlos filters at the UI with the slider + lane tabs), do NOT
        # hard-reject these — that silently killed 50 real jobs (federal IT +
        # Operations Manager roles) once the metro-substring bug was fixed. Only
        # a DEFINITE onsite/hybrid classification below is rejected.
        if reject_outside and not _mentions_metro(blob_low, metro):
            return "neutral", 1.0, cls, "unknown_work_mode_kept_for_review"
        return "neutral", 1.0, cls, ""

    if cls == "hybrid":
        if _mentions_metro(blob_low, metro):
            mult = metro_m if tech_ok() else 0.94
            return "accept", round(mult, 4), cls, "" if tech_ok() else "hybrid_metro_but_low_tech_signal"
        if not reject_outside:
            return "neutral", 0.97, cls, "hybrid_far_metro_policy_disabled"
        if not tech_ok():
            return "reject", 0.0, cls, "hybrid_far_metro_not_tech"
        return "reject", 0.0, cls, "hybrid_outside_allowed_metro"

    if cls == "onsite":
        if _mentions_metro(blob_low, metro):
            mult = metro_m if tech_ok() else 0.93
            return "accept", round(mult, 4), cls, "" if tech_ok() else "onsite_metro_low_tech"
        if not reject_outside:
            return "neutral", 0.95, cls, "onsite_far_policy_relaxed"
        return "reject", 0.0, cls, "onsite_outside_allowed_metro"

    return "accept", 1.0, cls, ""


# ---------------------------------------------------------------------------
# HARD INGEST LOCATION GATE
# ---------------------------------------------------------------------------
# Towns within ~30 minutes of Center City Philadelphia (Carlos at 1229 Chestnut
# St, 19107). Whole-state "pennsylvania"/"pa" is INTENTIONALLY excluded — PA
# spans 5 hours; a bare "PA" token matched Pittsburgh/Lancaster/Erie and let the
# entire state through as "metro". Keep this list to genuinely-commutable towns.
_PHILLY_METRO_TOWNS = frozenset(
    {
        "philadelphia", "philly", "greater philadelphia", "center city",
        # PA — Montgomery / Delaware / lower Bucks (≤~30 min)
        "bala cynwyd", "cynwyd", "conshohocken", "plymouth meeting", "norristown",
        "king of prussia", "jenkintown", "willow grove", "huntingdon valley",
        "abington", "glenside", "ardmore", "wynnewood", "bryn mawr", "narberth",
        "media, pa", "drexel hill", "upper darby", "havertown", "lansdowne",
        "bensalem", "feasterville", "trevose", "fort washington", "blue bell",
        "horsham", "elkins park", "cheltenham", "broomall", "newtown square",
        "wayne, pa", "radnor", "villanova", "gladwyne", "fort washington",
        "king of prussia, pa", "springfield, pa", "morton, pa", "folcroft",
        "essington", "tinicum", "darby", "yeadon", "collingdale", "ridley",
        # NJ — South Jersey near the bridges (≤~30 min)
        "camden, nj", "cherry hill", "mount laurel", "mt laurel", "moorestown",
        "maple shade", "pennsauken", "collingswood", "haddonfield", "haddon",
        "gloucester city", "marlton", "voorhees", "deptford", "west deptford",
        "bellmawr", "runnemede", "merchantville", "delran", "cinnaminson",
        "palmyra, nj", "riverton, nj", "riverside, nj", "gloucester, nj",
        "audubon, nj", "barrington, nj", "lawnside", "magnolia, nj", "somerdale",
    }
)

# In a structured location field, a bare "remote" is a true work-mode signal
# (location fields don't use it in a duty sense the way JD bullets do).
_LOCFIELD_REMOTE_RE = re.compile(
    r"\b(remote|anywhere|worldwide|work\s*from\s*home|wfh|distributed|telecommute)\b",
    re.IGNORECASE,
)


# US states OUTSIDE the Philly commute region (everything except PA / NJ / DE).
# Used to veto town-name collisions like "New Philadelphia, OH" or
# "Philadelphia, MS / TN". Abbreviations are only matched in a "City, ST"
# position (preceded by a comma) so the English words "in", "or", "me" don't
# trip it; full state names match on a word boundary.
_OUT_OF_REGION_STATE_RE = re.compile(
    r",\s*(?:al|ak|az|ar|ca|co|ct|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|mt|ne|nv|nh|nm|ny|nc|nd|oh|ok|or|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|dc)\b"
    r"|\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming)\b",
    re.IGNORECASE,
)


def _location_field_in_metro(loc_low: str) -> bool:
    """True if the (lower-cased) location string names a town within ~30 min of Philly."""
    if not loc_low:
        return False
    # Veto town-name collisions: if the field explicitly names a state outside
    # PA/NJ/DE, it is not Philly-metro no matter what city name appears.
    if _OUT_OF_REGION_STATE_RE.search(loc_low):
        return False
    for town in _PHILLY_METRO_TOWNS:
        if town in loc_low:
            return True
    # Philadelphia ZIP prefixes (191xx Philly proper, 190xx Delco) as a backstop.
    if re.search(r"\b191\d\d\b", loc_low):
        return True
    return False


# Vague / country-level location strings that carry no real geographic signal.
_VAGUE_LOC = frozenset(
    {
        "", "us", "u.s.", "u.s.a.", "usa", "united states",
        "united states of america", "north america", "anywhere", "various",
        "various locations", "multiple locations", "nationwide", "n/a",
        "remote", "remote us", "us remote",
    }
)

_US_STATE_NAMES = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new mexico", "new york", "north carolina",
        "north dakota", "ohio", "oklahoma", "oregon", "rhode island",
        "south carolina", "south dakota", "tennessee", "texas", "utah",
        "vermont", "virginia", "washington", "west virginia", "wisconsin",
        "wyoming", "district of columbia", "washington dc",
        # "pennsylvania" deliberately omitted — a bare "Pennsylvania" is treated
        # as concrete-non-metro below and rejected unless a metro town matched.
    }
)


def _location_field_is_concrete(loc_low: str) -> bool:
    """True if the location field names a specific place (city and/or state),
    as opposed to blank or a vague country-level value like ``US``."""
    s = (loc_low or "").strip().strip(".").strip()
    if not s or s in _VAGUE_LOC:
        return False
    s2 = re.sub(r",?\s*(?:us|u\.s\.|usa|united states)\s*$", "", s).strip().strip(",").strip()
    if not s2 or s2 in _VAGUE_LOC:
        return False
    if "," in s2:                       # "City, ST" form
        return True
    if s2 in _US_STATE_NAMES or s2 == "pennsylvania":
        return True
    return True                          # any other non-vague token = a place name


# Cached raw config (read once per process) so the per-upsert gate stays cheap.
_INGEST_GATE_CFG = None


def _gate_cfg():
    global _INGEST_GATE_CFG
    if _INGEST_GATE_CFG is None:
        import json
        import os

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        name = os.getenv("JOB_PIPELINE_CONFIG", "job_pipeline_config.json")
        path = name if os.path.isabs(name) else os.path.join(base, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                _INGEST_GATE_CFG = json.load(f)
        except Exception:
            _INGEST_GATE_CFG = {}
    return _INGEST_GATE_CFG


def ingest_location_allowed(
    title: str,
    location: str,
    desc: str,
    cfg: Dict[str, Any] | None = None,
) -> Tuple[bool, str]:
    """Hard gate run at ingest time: admit ONLY remote or true-Philly-metro jobs.

    Returns ``(allowed, reason)``. Rejects out-of-state onsite, far-PA onsite,
    and blank/unknown-location postings that carry no remote or metro signal
    (this is what was flooding the queue with federal + nationwide jobs).

    Fails OPEN: if the policy is disabled or anything is malformed, allow — the
    gate must never silently drop the entire ingest on a bug.
    """
    cfg = cfg if cfg is not None else _gate_cfg()
    loc_cfg = location_policy_settings(cfg)
    if not loc_cfg.get("enabled"):
        return True, "gate_disabled"

    loc_low = (location or "").lower()

    # 1. Remote / metro named directly in the structured location field — the
    #    strongest, least-ambiguous signal.
    if _LOCFIELD_REMOTE_RE.search(loc_low):
        return True, "remote:locfield"
    if _location_field_in_metro(loc_low):
        return True, "metro:locfield"

    # 2. The location field names a CONCRETE non-metro place (e.g. "Portland,
    #    OR", "Florida", "Oakmont, PA"). Trust it and reject — do NOT let a
    #    stray "remote"/"Philadelphia" phrase in the body override an explicit
    #    location. (A "Registered Nurse / Portland, OR" job is onsite Portland
    #    no matter what boilerplate the description carries.)
    if _location_field_is_concrete(loc_low):
        return False, f"out_of_area:{(location or '').strip()[:48]}"

    # 3. Location field is BLANK or VAGUE ("US", "United States"). Only accept
    #    on an EXPLICIT declarative "Location: …" line that says remote or names
    #    the metro. A bare "remote" word somewhere in the body is NOT enough —
    #    that misfired on field roles (nurse, 90%-travel tech) that carry stray
    #    remote-ish boilerplate.
    m = _DECLARATIVE_LOCATION_RE.search((desc or "")[:6000])
    if m:
        decl = m.group(1).lower()
        if _LOCFIELD_REMOTE_RE.search(decl):
            return True, "remote:declared"
        if _location_field_in_metro(decl):
            return True, "metro:declared"

    # 4. Blank/vague with no remote or metro signal — out of rules.
    label = (location or "").strip() or "blank"
    return False, f"out_of_area:{label[:48]}"
