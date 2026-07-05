"""Learning gaps cache: track JD requirements Carlos does NOT have grounded.

For every summarized job, we diff `key_requirements` (LLM-extracted from the JD)
against Carlos's grounded skill set (consolidated_profile.json + career_master.md
keywords). Anything not grounded gets recorded with frequency + recency so he can
prioritize what to learn (free certs, home lab, AI-assisted study).

Cache file: `learning_gaps.json` at repo root. Atomic writes (temp + rename) so
concurrent summarize calls don't corrupt the file.

Schema (v1):
{
  "schema_version": 1,
  "updated_at": "<iso>",
  "gaps": {
    "<normalized_keyword>": {
      "display": "<original phrasing>",
      "category": "cert" | "tool" | "skill" | "framework" | "years" | "other",
      "first_seen": "<iso-date>",
      "last_seen": "<iso-date>",
      "count": <int>,
      "samples": [{"item_id": N, "title": "...", "company": "..."}, ...]  # capped at 10
    },
    ...
  },
  "ignored": ["<normalized_keyword>", ...]  # user-marked: don't surface again
}
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# Repo-root sidecar (next to job_pipeline_config.json, evidence.json, etc.)
_GAPS_PATH = Path(__file__).resolve().parent.parent / "learning_gaps.json"

# In-process write lock — atomic-rename also protects across processes.
_WRITE_LOCK = threading.Lock()

_SCHEMA_VERSION = 1
_SAMPLES_CAP = 10

# Hand-curated categorization regex — matched in order, first hit wins.
# Tuples of (regex, category). Patterns are case-insensitive.
_CATEGORY_PATTERNS: List[tuple[re.Pattern, str]] = [
    # Years of experience — not a learnable skill, mostly filter noise
    (re.compile(r"^\d+\s*\+?\s*(?:years?|yrs?)\b", re.I), "years"),
    (re.compile(r"\b\d+\s*\+?\s*(?:years?|yrs?)\b", re.I), "years"),
    # Certifications (exam-shaped)
    (re.compile(r"\b(comptia|a\+|net\+|sec\+|s\+|n\+|server\+|cysa\+|pentest\+|cloud\+|linux\+|project\+)\b", re.I), "cert"),
    (re.compile(r"\b(ccna|ccnp|ccie|ccnt|ccst|cisco\s+certified)\b", re.I), "cert"),
    (re.compile(r"\b(aws\s+certified|az-\d+|ms-\d+|md-\d+|microsoft\s+certified|microsoft\s+fundamentals|azure\s+fundamentals)\b", re.I), "cert"),
    (re.compile(r"\b(itil\s+(?:4|foundation|certified)|itil\s+v\d|pmp|capm|csm|scrum\s+master)\b", re.I), "cert"),
    (re.compile(r"\b(rhce|rhcsa|cks|cka|gcp\s+certified|google\s+certified)\b", re.I), "cert"),
    (re.compile(r"\b(security\+|sec\+|cissp|ceh|gsec|gpen|oscp|chfi|sscp)\b", re.I), "cert"),
    (re.compile(r"\bcertif(?:ied|ication)\b", re.I), "cert"),
    # Frameworks / methodologies
    (re.compile(r"\b(agile|scrum|kanban|waterfall|safe|six\s*sigma|lean|devops|sre|itil)\b", re.I), "framework"),
    # Tools / products (vendors, software names)
    (re.compile(r"\b(active\s+directory|azure\s+ad|entra|okta|ldap|sccm|intune|jamf|workspace\s+one|mecm)\b", re.I), "tool"),
    (re.compile(r"\b(servicenow|jira|zendesk|freshservice|freshdesk|kayako|cherwell|manageengine|samanage|ivanti|solarwinds|datto|kaseya|connectwise|ninjaone)\b", re.I), "tool"),
    (re.compile(r"\b(splunk|datadog|new\s+relic|prometheus|grafana|elk|elasticsearch|kibana|sumo\s+logic|sentry)\b", re.I), "tool"),
    (re.compile(r"\b(aws|amazon\s+web\s+services|azure|gcp|google\s+cloud|kubernetes|k8s|docker|terraform|ansible|chef|puppet|saltstack)\b", re.I), "tool"),
    (re.compile(r"\b(vmware|esxi|vsphere|hyper-v|proxmox|nutanix|openstack)\b", re.I), "tool"),
    (re.compile(r"\b(m365|office\s+365|microsoft\s+365|outlook|teams|sharepoint|onedrive|exchange|powershell|bash|python|sql|mysql|postgres|mongodb)\b", re.I), "tool"),
    (re.compile(r"\b(workday|salesforce|hubspot|netsuite|sap|oracle|peoplesoft|tableau|power\s*bi|snowflake|databricks)\b", re.I), "tool"),
    (re.compile(r"\b(fortinet|palo\s+alto|cisco\s+(?:asa|firepower)|sonicwall|checkpoint|juniper|arista|meraki|aruba)\b", re.I), "tool"),
    # Skills (generic ability words)
    (re.compile(r"\b(troubleshoot|debug|monitor|configure|administer|install|deploy|scripting|automation|incident\s+response|root\s+cause)\b", re.I), "skill"),
]

# Things to NEVER surface as learning gaps (junk, generic soft-skills, etc.)
_STOP_PATTERNS: List[re.Pattern] = [
    re.compile(r"^(strong|excellent|good|great|effective)\s+(communication|written|verbal|interpersonal|customer\s+service)", re.I),
    re.compile(r"^(team\s+player|self[\s-]?starter|self[\s-]?motivated|attention\s+to\s+detail|problem[\s-]?solver|fast\s+learner)$", re.I),
    re.compile(r"^(bachelor|associate|high\s+school|ged|degree)", re.I),  # education not a skill
    re.compile(r"^(must|able|ability|willing|comfortable)\s+to\b", re.I),
    re.compile(r"^(remote|hybrid|onsite|on-site|in[\s-]?office|wfh)$", re.I),
    re.compile(r"^\$?\d+([,.]?\d+)*\s*(k|/?\s*(hr|hour|yr|year|annual))?$", re.I),  # salary
    re.compile(r"^\d+\s*\+?\s*(?:years?|yrs?)$", re.I),  # bare "5 years" / "3+ yrs" (not learnable)
    re.compile(r"^\d+[-\s]\d+\s*(?:years?|yrs?)$", re.I),  # "1-2 years"
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def normalize_keyword(s: str) -> str:
    """Normalize a JD-extracted requirement string to a cache key.

    - lowercase
    - strip leading "X years/yrs of " and trailing " experience/required/preferred/knowledge"
    - collapse whitespace
    - strip leading/trailing punctuation
    """
    if not s:
        return ""
    t = str(s).strip().lower()
    # Drop common qualifier suffixes
    t = re.sub(r"\s+(experience|required|preferred|knowledge|skills?|familiarity|exposure|background)\b.*$", "", t)
    # Drop leading "X+ years of "
    t = re.sub(r"^\d+\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?", "", t)
    # Drop "minimum X years" prefix
    t = re.sub(r"^(?:minimum|min\.?|at\s+least)\s+\d+\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?", "", t)
    # Common substitutions
    t = re.sub(r"\bms\s+365\b", "m365", t)
    t = re.sub(r"\bmicrosoft\s+365\b", "m365", t)
    t = re.sub(r"\boffice\s+365\b", "m365", t)
    t = re.sub(r"\bo365\b", "m365", t)
    t = re.sub(r"\bcomp\s*tia\b", "comptia", t)
    t = re.sub(r"\bk8s\b", "kubernetes", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    # Strip leading/trailing punctuation
    t = t.strip(".,;:!?-/\\()[]{}\"'")
    return t


def categorize(keyword: str) -> str:
    """Bucket a normalized keyword into cert/tool/skill/framework/years/other."""
    if not keyword:
        return "other"
    for pat, cat in _CATEGORY_PATTERNS:
        if pat.search(keyword):
            return cat
    return "other"


def is_stop_pattern(keyword: str) -> bool:
    """Return True if the keyword is too generic / not a learnable item."""
    if not keyword or len(keyword) < 2:
        return True
    for pat in _STOP_PATTERNS:
        if pat.search(keyword):
            return True
    return False


# ---------------------------------------------------------------------------
# Grounded-skill loading
# ---------------------------------------------------------------------------

_GROUNDED_CACHE: Optional[set[str]] = None
_GROUNDED_CACHE_MTIME: float = 0.0


def _load_grounded_skill_set(force: bool = False) -> set[str]:
    """Build a set of normalized keywords representing skills Carlos has
    grounded in consolidated_profile.json + career_master.md.

    Cached per-process; invalidated when either source file changes."""
    global _GROUNDED_CACHE, _GROUNDED_CACHE_MTIME
    base = Path(__file__).resolve().parent
    cp_path = base / "consolidated_profile.json"
    cm_path = base / "career_master.md"
    try:
        mtime = max(
            cp_path.stat().st_mtime if cp_path.exists() else 0,
            cm_path.stat().st_mtime if cm_path.exists() else 0,
        )
    except OSError:
        mtime = 0
    if _GROUNDED_CACHE is not None and not force and mtime <= _GROUNDED_CACHE_MTIME:
        return _GROUNDED_CACHE

    grounded: set[str] = set()
    try:
        if cp_path.exists():
            cp = json.loads(cp_path.read_text(encoding="utf-8"))
            sk = cp.get("skills") or {}
            for bucket in ("technical", "soft", "tools"):
                for item in sk.get(bucket) or []:
                    nk = normalize_keyword(str(item))
                    if nk:
                        grounded.add(nk)
            # certifications by name
            for c in cp.get("certifications") or []:
                if isinstance(c, dict):
                    nk = normalize_keyword(str(c.get("name") or ""))
                    if nk:
                        grounded.add(nk)
            # languages
            for l in cp.get("languages") or []:
                if isinstance(l, dict):
                    nk = normalize_keyword(str(l.get("language") or ""))
                    if nk:
                        grounded.add(nk)
    except (OSError, json.JSONDecodeError):
        pass

    # Cheap token sweep of career_master.md — split on commas + periods, add
    # short keyword-ish tokens. Crude but catches a lot.
    try:
        if cm_path.exists():
            txt = cm_path.read_text(encoding="utf-8")
            # Strip markdown headers/lists/code fences
            txt = re.sub(r"```.+?```", " ", txt, flags=re.S)
            txt = re.sub(r"[#>*_`\[\]()|]", " ", txt)
            for chunk in re.split(r"[.,;:\n]+", txt):
                c = chunk.strip().lower()
                if 2 <= len(c) <= 50 and re.search(r"[a-z]", c):
                    nk = normalize_keyword(c)
                    if nk and 2 <= len(nk) <= 50:
                        grounded.add(nk)
    except OSError:
        pass

    _GROUNDED_CACHE = grounded
    _GROUNDED_CACHE_MTIME = mtime
    return grounded


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _load_cache() -> Dict[str, Any]:
    if not _GAPS_PATH.exists():
        return {
            "schema_version": _SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "gaps": {},
            "ignored": [],
        }
    try:
        d = json.loads(_GAPS_PATH.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("not a dict")
        d.setdefault("schema_version", _SCHEMA_VERSION)
        d.setdefault("gaps", {})
        d.setdefault("ignored", [])
        return d
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "schema_version": _SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "gaps": {},
            "ignored": [],
        }


def _save_cache(d: Dict[str, Any]) -> None:
    d["updated_at"] = _now_iso()
    tmp = _GAPS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _GAPS_PATH)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _is_grounded(nk: str, grounded: set) -> bool:
    """Return True if normalized keyword `nk` matches grounded skills.

    Uses exact-match only. Substring matching was tried and rejected: the
    career_master.md token sweep includes negative mentions (Section 3 "Honest
    limits" — things Carlos explicitly does NOT have), so substring matches
    falsely grounded things like 'Active Directory' which is documented as
    "never touched". Exact match keeps the semantics tight; false negatives
    (e.g. JD says 'Veeam', grounded has only 'veeam backups') are fixed by
    adding the bare term to consolidated_profile.json tools, or by hitting
    the dashboard 'Ignore' button per-keyword."""
    return bool(nk) and nk in grounded


def update_learning_gaps(
    item_id: int,
    key_requirements: Iterable[str],
    *,
    job_title: str = "",
    company: str = "",
) -> int:
    """Diff requirements against grounded skills; append new ones to cache.

    Returns: number of NEW gap entries added this call (existing increments
    don't count). Safe to call from summarize.py for every summarized item."""
    reqs = [str(r).strip() for r in (key_requirements or []) if str(r).strip()]
    if not reqs:
        return 0

    grounded = _load_grounded_skill_set()

    with _WRITE_LOCK:
        cache = _load_cache()
        ignored = set(cache.get("ignored") or [])
        gaps: Dict[str, Any] = cache.get("gaps") or {}
        today = _today_iso()
        new_count = 0

        for raw in reqs:
            nk = normalize_keyword(raw)
            if not nk or is_stop_pattern(nk):
                continue
            if _is_grounded(nk, grounded):
                continue
            if nk in ignored:
                continue

            entry = gaps.get(nk)
            if entry is None:
                gaps[nk] = {
                    "display": raw[:120],
                    "category": categorize(nk),
                    "first_seen": today,
                    "last_seen": today,
                    "count": 1,
                    "samples": [{"item_id": item_id, "title": job_title[:120], "company": company[:80]}],
                }
                new_count += 1
            else:
                entry["count"] = int(entry.get("count") or 0) + 1
                entry["last_seen"] = today
                samples = list(entry.get("samples") or [])
                # avoid duplicate samples for the same item_id
                if not any(s.get("item_id") == item_id for s in samples):
                    samples.insert(0, {
                        "item_id": item_id,
                        "title": job_title[:120],
                        "company": company[:80],
                    })
                    samples = samples[:_SAMPLES_CAP]
                entry["samples"] = samples

        cache["gaps"] = gaps
        _save_cache(cache)
        return new_count


def top_gaps(n: int = 25, *, category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return top-N gaps by count, optionally filtered to one category.
    Each entry: {keyword, display, category, count, first_seen, last_seen, samples}."""
    cache = _load_cache()
    items: List[Dict[str, Any]] = []
    for k, v in (cache.get("gaps") or {}).items():
        if category and v.get("category") != category:
            continue
        items.append({
            "keyword": k,
            "display": v.get("display") or k,
            "category": v.get("category") or "other",
            "count": int(v.get("count") or 0),
            "first_seen": v.get("first_seen") or "",
            "last_seen": v.get("last_seen") or "",
            "samples": list(v.get("samples") or [])[:5],
        })
    items.sort(key=lambda x: (-x["count"], x["keyword"]))
    return items[: max(1, int(n))]


def category_counts() -> Dict[str, int]:
    """Return {category: count} aggregation for the dashboard summary."""
    cache = _load_cache()
    out: Dict[str, int] = {}
    for v in (cache.get("gaps") or {}).values():
        cat = v.get("category") or "other"
        out[cat] = out.get(cat, 0) + 1
    return out


def mark_ignored(keyword: str) -> bool:
    """Add a keyword to the ignored list and remove from gaps. Returns True
    if the keyword was found and ignored."""
    nk = normalize_keyword(keyword)
    if not nk:
        return False
    with _WRITE_LOCK:
        cache = _load_cache()
        ignored = list(cache.get("ignored") or [])
        if nk not in ignored:
            ignored.append(nk)
        cache["ignored"] = ignored
        gaps = cache.get("gaps") or {}
        removed = gaps.pop(nk, None)
        cache["gaps"] = gaps
        _save_cache(cache)
        return removed is not None


def mark_learned(keyword: str) -> bool:
    """Same as mark_ignored — keyword is removed from active gaps because user
    learned it (or has it grounded now). Future surfaces should be re-checked
    against the (updated) consolidated_profile.json + career_master.md instead."""
    return mark_ignored(keyword)


__all__ = [
    "update_learning_gaps",
    "top_gaps",
    "category_counts",
    "mark_ignored",
    "mark_learned",
    "normalize_keyword",
    "categorize",
]
