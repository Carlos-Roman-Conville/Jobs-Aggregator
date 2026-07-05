"""
Search preferences: hand-edited grounding from `search_preferences.md`,
applied as a final multiplier / auto-close pass after the location-policy
stage in `summarize_pipeline_item`.

Precedence pattern mirrors `career_master.md`:
  * The markdown file at `job_pipeline/search_preferences.md` is the
    authoritative source of truth. The Python module is a thin parser
    + scorer; it does not duplicate the values.
  * Loaded once per process via `load_search_preferences()` (cached).

Public API:
  * load_search_preferences() -> dict
  * score_posting_against_preferences(posting_dict) -> dict
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.location_policy import classify_remote_hybrid_on_site


def _parse_salary_low_usd(text: str) -> Optional[int]:
    """
    Local copy of `job_pipeline.ingest.parse_salary_low_usd` so this module
    stays importable without dragging in the DB layer (psycopg2). Mirror
    the source so future edits in ingest can be re-synced by hand.
    """
    if not (text or "").strip():
        return None
    s = text.lower().replace(",", "")
    m = re.search(r"(\d+)\s*k\b", s)
    if m:
        return int(m.group(1)) * 1000
    nums: List[int] = []
    for m in re.finditer(r"\b(\d{5,6})\b", s):
        nums.append(int(m.group(1)))
    if nums:
        return min(nums)
    return None


_BASE = Path(__file__).resolve().parent
_PREF_PATH = _BASE / "search_preferences.md"

# Module-level cache; reset by passing reload=True to load_search_preferences().
_CACHE: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Constants extracted from the .md (kept as code only for things the .md
# expresses as prose rather than parseable lists — e.g. the avoid-title
# regex compiled patterns, the proximity table values).
# ---------------------------------------------------------------------------

_DEFAULT_AVOID_TITLE_PATTERNS: Tuple[str, ...] = (
    r"\b(?:senior|sr\.?|principal|staff|vp|vice\s+president|head\s+of|supervisory)\b",
    # Bare "director" anywhere in title — narrow targeting cut canvass mgmt
    # (Field Director / Canvass Director) so broad reject is now safe.
    r"\bdirector\b",
    # "Director" handled separately — bare-word reject catches "Field Director" /
    # "Canvass Director" / "Organizing Director" which Carlos explicitly accepts.
    # Reject only senior-Director patterns.
    r"\b(?:executive|managing|associate|deputy|assistant|interim|acting)\s+director\b",
    r"\bdirector\s+of\b",
    r"\b(?:operations|engineering|technology|it|product|sales|marketing|finance|hr|talent|human\s+resources|customer\s+success|policy|legal|legislative|communications|program|brand|partnerships|business\s+development|strategy|growth|revenue|people)\s+director\b",
    r"^\s*director\s*(?:[-–—]|,|$)",
    # NOTE: "lead" alone was here previously; removed because it false-positives
    # on legitimate Ops/Shift Lead roles. Engineering Lead still caught via the
    # specific engineer rejects below.
    # TIER CAP for IC SUPPORT roles — Tier 1 only. Manager/Ops titles are NOT
    # "tiered" and pass through this filter. Specialist II is IC-tier and rejected.
    r"\btier\s*(?:2|ii|3|iii|two|three)\b",
    r"\blevel\s*(?:2|ii|3|iii|two|three)\b",
    r"\bspecialist\s*(?:2|ii|3|iii|two|three)\b",
    r"\b(?:l|lvl)\s*[23]\b",
    # IC engineering tiers II/III — over-leveled for Carlos's claim window
    r"\b(?:engineer|technician|analyst|administrator|developer|specialist|spec)\s*(?:ii|iii|2|3)\b",
    # "Tech Lead" / "Technical Lead" — senior IC leads in engineering teams
    r"\btech(?:nical)?\s+lead\b",
    # "Lead" prefix on any of Carlos's target families = senior IC, not Tier 1.
    # Scoped to target-family nouns so this doesn't false-positive on legitimate
    # Shift Lead / Ops Lead / Floor Lead (which Carlos accepts).
    r"\blead\s+(?:noc|help\s*desk|service\s*desk|desktop|deskside|customer\s+(?:support|service)|technical\s+support|tech\s+support|it\s+support|it\s+(?:specialist|spec|technician|analyst)|information\s+technology\s+(?:specialist|technician|analyst|spec)|systems?\s+support|client\s+support|end[\s-]?user\s+support|product\s+support)\b",
    # NOTE: "Operations Manager" was previously rejected unless prefixed with
    # technical/IT. Now accepted — Carlos has 4.5 years documented Ops Manager
    # experience (BTB + 1-800-GOT-JUNK).
    r"\bproject\s+manager\b(?!\s*[-–—]?\s*(?:technical|it))",
    r"\b(devops|sre|site\s+reliability)\s+engineer\b",
    r"\bcloud\s+(engineer|architect)\b",
    r"\b(software|backend|back-end|frontend|front-end|full[-\s]?stack)\s+engineer\b",
    r"\bsales\s+engineer\b",
    # Sales / Business Development / Account Mgmt — not for Carlos (no sales background)
    r"\b(?:account|enterprise|territory|regional)\s+executive\b",
    r"\b(?:account|enterprise|territory|regional|inside|outside|field)\s+sales(?:person)?\b",
    r"\bsales\s+(?:rep(?:resentative)?|manager|associate|specialist|consultant|coordinator|director|lead|executive|development)\b",
    r"\bbusiness\s+development\s+(?:rep(?:resentative)?|manager|director|associate|lead|executive)\b",
    r"\b(?:bdr|sdr)\b",
    r"\bnew\s+business\b",
    # NOTE: bare "Account Manager" intentionally NOT rejected — "Technical
    # Account Manager", "IT Account Manager", "Customer Success Account Manager"
    # are legitimate targets. Sales-flavored variants (Enterprise/Senior/Sales
    # Account Manager) are caught by other rules in this list.
    r"\bsolutions?\s+architect\b",
    r"\bservicenow\s+administrator\b",
    r"\b(iam|identity\s+and\s+access\s+management)\s+(engineer|analyst|administrator)\b",
    r"\bsecurity\s+engineer\b",
    r"\b(engineering|software)\s+manager\b",  # too senior — keep rejected
    r"\b(ai\s+trainer|data\s+annotation|data\s+labeler|ai\s+tutor|prompt\s+engineer)\b",
    r"\b(call\s+center|customer\s+service\s+rep)\b(?!.*\b(it|technical|sysadmin)\b)",
    r"\b(warehouse|fulfillment|logistics)\b",
    r"\bretail\b",
    # NOT GROUNDED — see career_master.md L199 + feedback_targeting.md
    r"\bphone\s+(repair|technician|tech)\b",
    r"\b(?:mobile|cellphone|cell\s*phone)\s+(repair|technician|tech)\b",
    r"\bmicrosolder",
    r"\bsmt\s+(?:rework|repair|technician)\b",
    r"\b(bga|ball\s*grid\s*array)\s+(rework|repair)\b",
    r"\balarm\s+(install|installer|installation)\b",  # security alarm install is NOT grounded
    # IC canvassing — Carlos accepts only canvass MANAGEMENT, not IC canvassing
    r"\bcanvasser\b",
    r"\bfield\s+organizer\b",  # typically heavy IC canvassing
    r"\bfield\s+representative\b",
    r"\b(?:door[-\s]?to[-\s]?door|d2d)\b",
    r"\bpetitioner\b",
    r"\b(?:signature|petition)\s+gatherer\b",
    r"\bphone\s+bank(?:er|ing)?\b",
)

_CLEARANCE_RE = re.compile(
    r"\b(?:security\s+clearance|secret\s+clearance|top\s+secret|clearance\s+required|"
    r"active\s+clearance|public\s+trust|background\s+investigation)\b",
    re.I,
)

_DEFAULT_NOISE_PATTERNS: Tuple[str, ...] = (
    r"\bai\s+trainer\b",
    r"\bdata\s+annotation\b",
    r"\bdata\s+labeler\b",
    r"\b1099\s+contractor\b",
    r"\bcommission\s+only\b",
    r"\bmlm\b",
    r"\bmulti[-\s]?level\s+marketing\b",
    r"\bunpaid\b",
    r"\bintern(?:ship)?\b",
)

_TIER1_TITLE_PATTERNS: Tuple[str, ...] = (
    # PRIMARY targets — Help Desk, Customer Support, Jr IT, Desktop Support, Tier 1 NOC.
    # Help Desk family
    r"\bhelp\s*desk(?:\s+(?:i|1|analyst|technician|specialist|agent|support|representative|engineer))?\b",
    r"\bservice\s+desk(?:\s+(?:i|1|analyst|technician|agent))?\b",
    # Customer Support / Technical Support (tech-flavored)
    r"\bcustomer\s+(?:support|service)\s+(?:engineer|technician|specialist|representative|rep|analyst|agent)(?:\s+(?:i|1))?\b",
    r"\btechnical\s+support\s+(?:specialist|engineer|representative|rep|technician|agent|analyst)(?:\s+(?:i|1))?\b",
    r"\btech\s+support(?:\s+(?:i|1|specialist|engineer|rep|representative|technician|agent))?\b",
    r"\bproduct\s+support\s+(?:specialist|engineer|representative|rep)(?:\s+(?:i|1))?\b",
    # Junior IT / IT Support I
    r"\b(?:jr\.?|junior)\s+it(?:\s+support)?\b",
    r"\bit\s+support(?:\s+(?:i|1|specialist|technician|analyst|representative|engineer|agent))?\b",
    r"\bit\s+(?:specialist|technician|analyst)(?:\s+(?:i|1))?\b",
    # USAJOBS abbreviation: "IT SPEC (CUSTSPT)" / "IT SPEC (PLCYPLN)". Same role
    # family as IT Specialist; federal vacancy boards routinely shorten it.
    r"\bit\s+spec\b",
    # IT [word] Support pattern — "IT Field Support Technician", "IT Floor
    # Support", "IT Office Support". Desktop-support variants with a qualifier
    # in the middle.
    r"\bit\s+\w+\s+support(?:\s+(?:technician|specialist|engineer|analyst))?\b",
    # "Technical Support Engineering" — gerund variant of Technical Support Engineer.
    r"\btechnical\s+support\s+engineering\b",
    # USAJOBS 2210 long-form. BA Cum Laude qualifies for GS-7 via Superior
    # Academic Achievement; vet preference applies. Federal civilian IT on-ramp.
    r"\binformation\s+technology\s+specialist\b",
    # Tyler-Tech-style customer-support/help-desk variant. Already validated —
    # Carlos submitted to Tyler 2026-05-30.
    r"\bsupport\s+services\s+representative\b",
    # Systems Support — Tier 1 help-desk role by another name. Carlos's BTB
    # tech ops + BA + Reserve profile lands here cleanly.
    r"\bsystems?\s+support\s+(?:specialist|technician|analyst|representative|rep|engineer|agent)(?:\s+(?:i|1))?\b",
    # Client Support / End-user Support — same job family as Help Desk.
    r"\b(?:client|end[\s-]?user)\s+support(?:\s+(?:specialist|technician|analyst|representative|rep|engineer|agent))?\b",
    # Desktop Support
    r"\bdesktop\s+support(?:\s+(?:i|1|specialist|technician|engineer|analyst))?\b",
    r"\bdeskside\s+support\b",
    r"\bend[\s-]?user\s+support\b",
    # NOC Tier 1
    r"\bnoc\s+(?:i|1|tier\s*(?:i|1|one)|level\s*(?:i|1|one)|analyst|technician|engineer|operator)(?:\s+(?:i|1))?\b",
    r"\b(?:jr\.?|junior)\s+noc\b",
    # BACKUP — Operations Manager family (still in scope, lower priority)
    r"\boperations\s+manager\b",
    r"\bops\s+manager\b",
    r"\btechnical\s+operations\s+manager\b",
    r"\bit\s+operations\s+manager\b",
    r"\b(?:office|shift|floor|venue|restaurant|general|store)\s+manager\b",
    r"\b(?:help\s*desk|service\s*desk|support)\s+manager\b",
    r"\bit\s+manager\b",
)

# Same patterns as TIER 1 but used as the HARD INGEST PRE-FILTER.
# Anything that doesn't match here AND isn't allowed by the avoid regex
# never makes it into the DB. Cuts noise + LLM cost at the source.
_TARGET_TITLE_PATTERNS: Tuple[str, ...] = _TIER1_TITLE_PATTERNS

# Primary targets get an extra boost over Ops Manager backup at ranking time.
_PRIMARY_TITLE_PATTERNS: Tuple[str, ...] = (
    r"\bhelp\s*desk\b",
    r"\bservice\s+desk\b",
    r"\bcustomer\s+(?:support|service)\b",
    r"\btechnical\s+support\b",
    r"\btech\s+support\b",
    r"\b(?:jr\.?|junior)\s+it\b",
    r"\bit\s+support\b",
    r"\bdesktop\s+support\b",
    r"\bdeskside\s+support\b",
    r"\bend[\s-]?user\s+support\b",
    r"\bnoc\s+(?:i|1|analyst|technician|engineer|operator|tier\s*(?:i|1|one)|level\s*(?:i|1|one))\b",
)

# Used for the onsite-Philly check (require tech-title for onsite acceptance).
# Same narrow scope as TIER 1 — Carlos only wants the 5 primary targets + Ops as backup.
_TECH_TITLE_PATTERNS: Tuple[str, ...] = _TIER1_TITLE_PATTERNS

_GROWTH_SIGNAL_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("ladder",       r"\b(junior|jr\.?)\s*(?:->|→|→)\s*mid\s*(?:->|→|→)\s*senior\b"),
    ("ladder",       r"\b(promotion\s+path|career\s+development\s+plan|growth\s+track)\b"),
    ("certs",        r"\b(certification\s+reimbursement|cert\s+reimbursement)\b"),
    ("certs",        r"\b(comptia|cisco|microsoft|red\s*hat|aws)\s+(certification|certs?)\b"),
    ("mentorship",   r"\b(paired\s+with\s+senior|shadow\s+rotation|rotation\s+program|mentorship)\b"),
    ("cross_train",  r"\b(cross[-\s]?train(?:ing|ed)?|cross[-\s]?functional\s+exposure)\b"),
    ("path_to",      r"\bpath\s+to\s+(systems?\s+administration|network\s+engineering|security|devops)\b"),
)

_VET_LANE_PATTERNS: Tuple[str, ...] = (
    r"\busajobs(\.gov)?\b",
    r"\bveteran(?:s|'s)?\s+preference\b",
    r"\bvra\b|\bvrap\b",
)

# Proximity table — distance bands and multipliers from the .md.
_PROXIMITY_TABLE: Tuple[Tuple[float, float, float, str], ...] = (
    (0.0,   5.0,  1.10, "inner_core_0_5mi"),
    (5.0,  10.0,  1.07, "near_ring_5_10mi"),
    (10.0, 20.0,  1.04, "mid_ring_10_20mi"),
    (20.0, 30.0,  1.01, "outer_ring_20_30mi"),
)

# Coarse location string -> approximate miles from 19107.
_LOCATION_MILES_FROM_19107: Dict[str, float] = {
    # Inner core (0-5)
    "philadelphia":         0.0,
    "center city":          0.0,
    "philadelphia, pa":     0.0,
    "south philadelphia":   3.0,
    "south philly":         3.0,
    "north philadelphia":   3.5,
    "fishtown":             2.5,
    "northern liberties":   1.5,
    "university city":      2.0,
    "old city":             0.5,
    # Near ring (5-10)
    "west philadelphia":    5.5,
    "manayunk":             8.0,
    "bala cynwyd":          7.5,
    "upper darby":          7.0,
    "camden":               4.5,
    "camden, nj":           4.5,
    "pennsauken":           7.0,
    "pennsauken, nj":       7.0,
    # Mid ring (10-20)
    "cherry hill":          11.0,
    "cherry hill, nj":      11.0,
    "conshohocken":         13.0,
    "plymouth meeting":     14.0,
    "media":                12.0,
    "media, pa":            12.0,
    "bensalem":             17.0,
    "bensalem, pa":         17.0,
    "norristown":           17.0,
    "ardmore":              10.0,
    # Outer ring (20-30)
    "king of prussia":      20.0,
    "king of prussia, pa":  20.0,
    "fort washington":      18.0,
    "fort washington, pa":  18.0,
    "wilmington":           28.0,
    "wilmington, de":       28.0,
    "princeton":            40.0,
    "princeton, nj":        40.0,
    # Common rejected cities
    "new york":             95.0,
    "new york, ny":         95.0,
    "boston":               300.0,
    "san francisco":        2900.0,
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _read_pref_md() -> str:
    override = (os.getenv("JOB_PIPELINE_SEARCH_PREFS_PATH") or "").strip()
    p = Path(override) if override else _PREF_PATH
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _split_sections(md: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    cur_key = ""
    cur_buf: List[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur_key:
                sections[cur_key] = "\n".join(cur_buf).strip()
            cur_key = re.sub(r"\s+", " ", m.group(1).strip().lower())
            cur_buf = []
        else:
            cur_buf.append(line)
    if cur_key:
        sections[cur_key] = "\n".join(cur_buf).strip()
    return sections


def _section(sections: Dict[str, str], *names: str) -> str:
    for n in names:
        key = n.strip().lower()
        if key in sections:
            return sections[key]
    for k, v in sections.items():
        for n in names:
            if n.lower() in k:
                return v
    return ""


def _parse_salary_floors(sections: Dict[str, str]) -> Dict[str, int]:
    """
    Parse hard salary floors per work mode from the 'Salary floors' section.

    Remote may declare "no hard floor"; in that case the floor is 0 and
    salary alone never auto-closes a remote posting (the noise filter
    still rejects "unpaid" / "commission only" / etc.).
    """
    body = _section(sections, "salary floors")
    # Defaults reflect the current .md spec (remote=0, hybrid=onsite=50k).
    out = {"remote": 0, "remote_flex": 0, "hybrid": 50000, "onsite": 50000}
    # If the remote line says "no hard floor", keep 0; otherwise look for $X.
    remote_body = body
    remote_no_floor = bool(
        re.search(r"-?\s*remote\s*:\s*no\s+hard\s+floor", remote_body, flags=re.IGNORECASE)
    )
    if not remote_no_floor:
        m = re.search(r"-?\s*remote\s*:\s*\$([\d,]+)", body, flags=re.IGNORECASE)
        if m:
            out["remote"] = int(m.group(1).replace(",", ""))
    for mode in ("hybrid", "onsite"):
        m = re.search(rf"-?\s*{mode}\s*:\s*\$([\d,]+)", body, flags=re.IGNORECASE)
        if m:
            out[mode] = int(m.group(1).replace(",", ""))
    # Optional "flexibility down to $X" (legacy field; only honored if present).
    m = re.search(r"flexibility\s+down\s+to\s+\$([\d,]+)", body, flags=re.IGNORECASE)
    if m:
        out["remote_flex"] = int(m.group(1).replace(",", ""))
    return out


def _parse_strong_preference_salary(sections: Dict[str, str]) -> int:
    """
    Parse the strong-preference salary threshold from the 'Salary preference'
    section. Default $70,000 if the section is absent or unparseable.
    """
    body = _section(sections, "salary preference", "salary preference (soft boost")
    if not body:
        return 70000
    m = re.search(r"\$([\d,]+)\s*/?\s*year", body, flags=re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"\*\*\$([\d,]+)", body)
    if m:
        return int(m.group(1).replace(",", ""))
    return 70000


def _parse_seed_terms(sections: Dict[str, str]) -> List[str]:
    body = _section(sections, "search-term seed list", "search term seed list")
    terms: List[str] = []
    for m in re.finditer(r'-\s*"([^"]+)"', body):
        s = m.group(1).strip()
        if s:
            terms.append(s)
    return terms


def _parse_radius_miles(sections: Dict[str, str]) -> float:
    body = _section(sections, "geography constraints")
    m = re.search(r"(\d+)\s*[-–—]?\s*mile\s+radius|radius\s*:\s*(\d+)", body, flags=re.IGNORECASE)
    if m:
        for grp in m.groups():
            if grp:
                return float(grp)
    return 30.0


def search_term_seeds() -> List[str]:
    """Return parsed seed phrases from search_preferences.md (may be empty)."""
    return list(load_search_preferences().get("search_term_seeds") or [])


def load_search_preferences(*, reload: bool = False) -> Dict[str, Any]:
    """Load and parse `search_preferences.md` once per process."""
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    md = _read_pref_md()
    sections = _split_sections(md) if md else {}

    pref = {
        "raw_md": md,
        "sections": sections,
        "salary_floors": _parse_salary_floors(sections) if sections else {
            "remote": 0, "remote_flex": 0, "hybrid": 50000, "onsite": 50000,
        },
        "strong_preference_salary": _parse_strong_preference_salary(sections) if sections else 70000,
        "metro_radius_miles": _parse_radius_miles(sections) if sections else 30.0,
        "search_term_seeds": _parse_seed_terms(sections),
        "avoid_title_re": re.compile("|".join(_DEFAULT_AVOID_TITLE_PATTERNS), re.IGNORECASE),
        "noise_body_re": re.compile("|".join(_DEFAULT_NOISE_PATTERNS), re.IGNORECASE),
        "tier1_title_re": re.compile("|".join(_TIER1_TITLE_PATTERNS), re.IGNORECASE),
        "tech_title_re": re.compile("|".join(_TECH_TITLE_PATTERNS), re.IGNORECASE),
        "target_title_re": re.compile("|".join(_TARGET_TITLE_PATTERNS), re.IGNORECASE),
        "primary_title_re": re.compile("|".join(_PRIMARY_TITLE_PATTERNS), re.IGNORECASE),
        "vet_lane_re": re.compile("|".join(_VET_LANE_PATTERNS), re.IGNORECASE),
        "growth_signal_re": [
            (label, re.compile(pat, re.IGNORECASE)) for (label, pat) in _GROWTH_SIGNAL_PATTERNS
        ],
        "proximity_table": _PROXIMITY_TABLE,
        "location_miles_table": dict(_LOCATION_MILES_FROM_19107),
    }
    _CACHE = pref
    return pref


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _coalesce_str(d: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _annualize(salary_text: str) -> Optional[int]:
    """Annualize at 2080 h/yr if hourly; else fall back to parse_salary_low_usd."""
    if not salary_text:
        return None
    s = salary_text.lower()
    m = re.search(r"\$?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s*per\s*)?\s*(hr|hour|hourly)\b", s)
    if m:
        try:
            rate = float(m.group(1))
            return int(round(rate * 2080))
        except ValueError:
            pass
    return _parse_salary_low_usd(salary_text)


def _distance_miles(location: str, miles_table: Dict[str, float]) -> Optional[float]:
    if not location:
        return None
    loc_low = location.lower().strip()
    if loc_low in miles_table:
        return miles_table[loc_low]
    best: Optional[float] = None
    for key, miles in miles_table.items():
        if key in loc_low:
            if best is None or miles < best:
                best = miles
    return best


def _proximity_multiplier(miles: Optional[float], proximity_table) -> Tuple[float, str]:
    if miles is None:
        return 1.0, "unknown_distance_no_boost"
    for lo, hi, mult, label in proximity_table:
        if lo <= miles < hi:
            return mult, label
    return 1.0, "outside_table_no_boost"


def _mode_to_salary_floor(mode: str, floors: Dict[str, int], jd_lower: str) -> Tuple[int, str]:
    if mode == "remote":
        growth_present = any(
            pat.search(jd_lower) for (_lbl, pat) in load_search_preferences()["growth_signal_re"]
        )
        if growth_present:
            return floors["remote_flex"], "remote_flex_growth_signal"
        return floors["remote"], "remote_standard"
    if mode == "hybrid":
        return floors["hybrid"], "hybrid_standard"
    if mode == "onsite":
        return floors["onsite"], "onsite_standard"
    return floors["remote"], "unknown_mode_fallback_remote_floor"


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------

def score_posting_against_preferences(posting: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply hand-edited search preferences to a posting.

    Returns a dict with pref_multiplier, auto_close_reason, boost_signals,
    preference_notes, work_mode, distance_miles_from_19107, salary_low_usd,
    salary_floor_applied. See module docstring for the full rule order.
    """
    prefs = load_search_preferences()

    title       = _coalesce_str(posting, "title", "role")
    desc        = _coalesce_str(posting, "description_text", "description", "desc")
    location    = _coalesce_str(posting, "location")
    salary_text = _coalesce_str(posting, "salary_text", "salary")
    source      = _coalesce_str(posting, "source").lower()

    title_l = title.lower()
    desc_l  = desc.lower()
    loc_l   = location.lower()
    blob    = f"{title_l}\n{loc_l}\n{desc_l}"

    floors        = prefs["salary_floors"]
    avoid_re      = prefs["avoid_title_re"]
    noise_re      = prefs["noise_body_re"]
    tier1_re      = prefs["tier1_title_re"]
    tech_title_re = prefs["tech_title_re"]
    vet_re        = prefs["vet_lane_re"]
    growth_pairs  = prefs["growth_signal_re"]
    miles_table   = prefs["location_miles_table"]
    proximity_tbl = prefs["proximity_table"]
    radius        = float(prefs["metro_radius_miles"])

    notes: List[str] = []
    boosts: List[str] = []

    work_mode = classify_remote_hybrid_on_site(title, location, desc)
    # Belt-and-suspenders: sparse federal postings may still classify as unknown without telework wording.
    if work_mode == "unknown" and source == "usajobs":
        work_mode = "onsite"
    miles = _distance_miles(location, miles_table)
    salary_low = _annualize(salary_text) or _annualize(desc) or None
    salary_floor, floor_note = _mode_to_salary_floor(work_mode, floors, desc_l)

    result: Dict[str, Any] = {
        "pref_multiplier": 1.0,
        "auto_close_reason": None,
        "boost_signals": boosts,
        "preference_notes": notes,
        "work_mode": work_mode,
        "distance_miles_from_19107": miles,
        "salary_low_usd": salary_low,
        "salary_floor_applied": salary_floor,
    }

    # Rule 1: title avoid
    if title and avoid_re.search(title_l):
        result["auto_close_reason"] = "title_avoided"
        notes.append(f"Title '{title}' matched the hard-reject avoid list.")
        return result

    # Rule 2: salary floor
    if salary_low is not None and salary_low < salary_floor:
        result["auto_close_reason"] = "salary_below_floor"
        notes.append(
            f"Salary low ${salary_low:,} below {work_mode} floor ${salary_floor:,} ({floor_note})."
        )
        return result

    # Rule 3: geography for non-remote
    if work_mode in ("hybrid", "onsite"):
        if miles is not None and miles > radius:
            result["auto_close_reason"] = "outside_metro"
            notes.append(
                f"{work_mode.title()} posting at ~{miles:.0f} mi from 19107 (> {radius:.0f} mi)."
            )
            return result
        if work_mode == "onsite":
            if not tech_title_re.search(title_l):
                result["auto_close_reason"] = "outside_metro"
                notes.append(
                    f"Onsite needs a technical title; '{title}' did not match the tech-title regex."
                )
                return result
            if salary_low is not None and salary_low < floors["onsite"]:
                result["auto_close_reason"] = "salary_below_floor"
                notes.append(
                    f"Onsite floor ${floors['onsite']:,}; posting offered ${salary_low:,}."
                )
                return result

    # Rule 4: noise (title or JD body; captures standalone "Intern" in title)
    noise_blob_td = f"{title_l}\n{desc_l}"
    if noise_blob_td.strip() and noise_re.search(noise_blob_td):
        result["auto_close_reason"] = "noise_filtered"
        notes.append("Title or JD matched a noise-filter pattern.")
        return result

    # Rule 4b: years-of-experience gap auto-close.
    # Closes postings whose stated minimum experience exceeds what Carlos can
    # honestly claim (career_profile.constraints.claim_years_technical_experience)
    # plus an allowed gap (career_profile.constraints.max_apply_min_years_experience_gap).
    # Local imports keep module import order intact (no DB at module load).
    try:
        from job_pipeline.ats_score import extract_min_years_experience
        from job_pipeline.domain_fit import load_career_profile

        prof = load_career_profile()
        con = prof.get("constraints") if isinstance(prof.get("constraints"), dict) else {}
        claim_years = int((con or {}).get("claim_years_technical_experience", 2) or 0)
        max_gap = int((con or {}).get("max_apply_min_years_experience_gap", 2) or 0)
        ceiling = claim_years + max_gap  # default 4

        jd_years, jd_span = extract_min_years_experience(desc)
        if jd_years is not None and jd_years > ceiling:
            result["auto_close_reason"] = "years_gap_too_wide"
            notes.append(
                f"JD requires ~{jd_years} years experience (matched '{jd_span}'); "
                f"profile ceiling is {ceiling} (claim={claim_years} + gap={max_gap})."
            )
            return result
    except Exception:
        # Years-gap rule is advisory — never block scoring on parser/profile failures.
        pass

    # Rule 5: soft boosts
    mult = 1.0

    if work_mode == "remote":
        mult *= 1.25
        boosts.append("remote_1.25x")
    elif work_mode == "hybrid":
        if miles is None or miles <= radius:
            mult *= 1.10
            boosts.append("hybrid_in_metro_1.10x")
    elif work_mode == "onsite":
        if (miles is None or miles <= radius) and tech_title_re.search(title_l):
            if salary_low is None or salary_low >= floors["onsite"]:
                mult *= 1.05
                boosts.append("onsite_metro_tech_above_floor_1.05x")

    if tier1_re.search(title_l):
        mult *= 1.10
        boosts.append("tier1_title_family_1.10x")

    # Primary-target prioritization: the 5 primary titles (Help Desk, Customer Support,
    # Jr IT / IT Support, Desktop Support, Tier 1 NOC) get an EXTRA boost over the Ops
    # Manager backup family. Carlos explicitly prioritized these in the targeting.
    primary_re = prefs.get("primary_title_re")
    if primary_re is not None and primary_re.search(title_l):
        mult *= 1.12
        boosts.append("primary_target_title_1.12x")

    # Strong-preference salary boost — applies to any work mode when
    # salary meets the hand-edited threshold (default $70k). Lets the
    # ranker prefer higher-paying postings without auto-closing lower ones.
    strong_pref_salary = int(prefs.get("strong_preference_salary") or 70000)
    if salary_low is not None and salary_low >= strong_pref_salary:
        mult *= 1.15
        boosts.append(f"salary_ge_{strong_pref_salary // 1000}k_1.15x")
        notes.append(
            f"Salary preference: ${salary_low:,} meets the ${strong_pref_salary:,} target."
        )

    if work_mode in ("hybrid", "onsite") and miles is not None and miles <= radius:
        prox_mult, prox_label = _proximity_multiplier(miles, proximity_tbl)
        if prox_mult > 1.0:
            mult *= prox_mult
            boosts.append(f"proximity_{prox_label}_{prox_mult:.2f}x")
            notes.append(f"Proximity boost: ~{miles:.0f} mi from 19107 ({prox_label}).")

    if source == "usajobs" or vet_re.search(blob):
        if _CLEARANCE_RE.search(blob):
            mult *= 0.78
            boosts.append("clearance_required_0.78x")
            notes.append("Clearance-required posting down-ranked.")
        elif source == "usajobs" and not tier1_re.search(title_l):
            mult *= 0.85
            boosts.append("federal_generic_0.85x")
            notes.append("Generic federal IT role down-ranked vs remote support targets.")
        elif vet_re.search(blob):
            mult *= 1.05
            boosts.append("vet_lane_1.05x")
            notes.append("Veteran-preference lane boost applied.")

    growth_hits: List[str] = []
    for label, pat in growth_pairs:
        if pat.search(desc_l):
            growth_hits.append(label)
    if growth_hits:
        unique = sorted(set(growth_hits))[:4]
        bump = 1.0 + 0.02 * len(unique)
        mult *= bump
        boosts.append(f"growth_signals_{'_'.join(unique)}_{bump:.2f}x")

    # Clamp range widened to 2.00 so strong-but-not-perfect items differentiate
    # in the 1.4-1.8 band instead of saturating against the old 1.85 ceiling.
    mult = max(0.50, min(2.00, mult))
    result["pref_multiplier"] = round(mult, 4)
    return result


def passes_target_title_filter(title: str) -> bool:
    """Hard ingest pre-screen: title must match the narrow target list
    (Carlos's 5 primaries + Ops Manager backup) AND must NOT match the avoid
    regex (Senior/Sales/Director/Tier 2+/etc.).

    Use this as a final guard before any source upserts a posting to the DB.
    Rejecting here saves DB rows, summarize LLM calls, and review time on
    guaranteed-not-a-fit jobs.

    Returns True if the posting's title is in scope, False if it should be
    dropped at ingest. Empty/None titles default to True (let downstream
    validation handle them).
    """
    if not title:
        return True
    t = title.lower().strip()
    if not t:
        return True
    try:
        prefs = load_search_preferences()
    except Exception:
        return True
    target_re = prefs.get("target_title_re")
    avoid_re = prefs.get("avoid_title_re")
    if target_re is not None and not target_re.search(t):
        return False
    if avoid_re is not None and avoid_re.search(t):
        return False
    return True


__all__ = [
    "load_search_preferences",
    "score_posting_against_preferences",
    "search_term_seeds",
    "passes_target_title_filter",
]
