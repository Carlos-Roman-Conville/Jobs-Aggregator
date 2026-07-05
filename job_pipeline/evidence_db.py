"""Structured evidence store for truth-controlled resume optimization."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.rendercv_export import normalize_rendercv_date

_EVIDENCE_CATEGORIES = ("systems", "support", "networking", "documentation", "metrics", "truth_limits")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def evidence_json_path() -> Path:
    override = (os.getenv("JOB_PIPELINE_EVIDENCE_PATH") or "").strip()
    if override:
        return Path(override)
    return _repo_root() / "evidence.json"


@lru_cache(maxsize=1)
def load_evidence_db() -> Dict[str, Any]:
    path = evidence_json_path()
    if not path.is_file():
        return {"employers": {}, "global_metrics": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"employers": {}, "global_metrics": []}
    return data if isinstance(data, dict) else {"employers": {}, "global_metrics": []}


def clear_evidence_cache() -> None:
    load_evidence_db.cache_clear()


def _norm_employer_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def match_employer_key(company: str) -> Optional[str]:
    """Resolve experience company string to evidence.json employer key."""
    blob = (company or "").lower()
    if not blob:
        return None
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    for key, rec in employers.items():
        if not isinstance(rec, dict):
            continue
        names = [str(rec.get("display_name") or "")]
        names.extend(str(a) for a in (rec.get("aliases") or []) if a)
        names.append(key.replace("_", " "))
        for n in names:
            n_l = (n or "").lower()
            if n_l and (n_l in blob or blob in n_l):
                return key
            nk = _norm_employer_key(n)
            if nk and nk in _norm_employer_key(blob):
                return key
    return None


def employer_record(key: str) -> Dict[str, Any]:
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    rec = employers.get(key)
    return dict(rec) if isinstance(rec, dict) else {}


def all_evidence_blobs() -> str:
    """Flattened searchable text from the evidence DB."""
    db = load_evidence_db()
    parts: List[str] = []
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    for rec in employers.values():
        if not isinstance(rec, dict):
            continue
        for cat in _EVIDENCE_CATEGORIES:
            for item in rec.get(cat) or []:
                parts.append(str(item))
    for m in db.get("global_metrics") or []:
        parts.append(str(m))
    return "\n".join(parts).lower()


def truth_limits_blob() -> List[str]:
    out: List[str] = []
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    for rec in employers.values():
        if not isinstance(rec, dict):
            continue
        out.extend(str(x) for x in (rec.get("truth_limits") or []) if str(x).strip())
    return out


def metrics_for_employer(key: str) -> List[str]:
    rec = employer_record(key)
    return [str(m).strip() for m in (rec.get("metrics") or []) if str(m).strip()]


def metric_display_for_employer(key: str) -> List[str]:
    """Recruiter-facing polished metric bullets; falls back to raw metrics."""
    rec = employer_record(key)
    display = [str(m).strip() for m in (rec.get("metric_display") or []) if str(m).strip()]
    if display:
        return display
    return metrics_for_employer(key)


def global_metrics() -> List[str]:
    db = load_evidence_db()
    return [str(m).strip() for m in (db.get("global_metrics") or []) if str(m).strip()]


def evidence_prompt_block() -> str:
    """Compact block for LLM prompts — facts + limits."""
    db = load_evidence_db()
    employers = db.get("employers") if isinstance(db.get("employers"), dict) else {}
    if not employers:
        return ""
    lines = ["EVIDENCE DATABASE (only source for claims beyond PROFILE_TEXT):"]
    for key, rec in employers.items():
        if not isinstance(rec, dict):
            continue
        lines.append(f"- {rec.get('display_name') or key}:")
        for cat in ("systems", "support", "networking", "documentation", "metrics"):
            items = rec.get(cat) or []
            if items:
                lines.append(f"  {cat}: {', '.join(str(i) for i in items[:12])}")
        limits = rec.get("truth_limits") or []
        if limits:
            lines.append(f"  truth_limits: {'; '.join(str(l) for l in limits[:5])}")
    return "\n".join(lines)


def apply_parser_safe_experience(content: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Normalize experience entries for ATS-friendly single-line company/title/date structure.
    """
    notes: List[str] = []
    exps = content.get("experience")
    if not isinstance(exps, list):
        return content, notes

    for exp in exps:
        if not isinstance(exp, dict):
            continue
        company = str(exp.get("company") or "").strip()
        key = match_employer_key(company)
        if key:
            rec = employer_record(key)
            if rec.get("title_canonical"):
                old = str(exp.get("title") or "")
                canon = str(rec["title_canonical"])
                if old and old != canon:
                    notes.append(f"parser-safe title: {company} -> {canon}")
                exp["title"] = canon
            if rec.get("location") and not str(exp.get("location") or "").strip():
                exp["location"] = str(rec["location"])
            dr = str(rec.get("date_range") or "").strip()
            if dr and not str(exp.get("start_date") or "").strip():
                m = re.match(
                    r"([A-Za-z]{3,9}\s+\d{4})\s*[–\-—]\s*([A-Za-z]{3,9}\s+\d{4}|\d{4}|present)",
                    dr,
                    re.I,
                )
                if m:
                    exp["start_date"] = normalize_rendercv_date(m.group(1))
                    exp["end_date"] = normalize_rendercv_date(m.group(2))
                else:
                    exp["duration"] = dr
                notes.append(f"parser-safe dates for {company}")
        for field in ("start_date", "end_date"):
            if exp.get(field):
                exp[field] = normalize_rendercv_date(str(exp[field]))
    return content, notes
