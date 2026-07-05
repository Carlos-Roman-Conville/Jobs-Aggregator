"""Post-build regression check — fails LOUD when known bad patterns ship.

The build pipeline runs deterministic scrubbers (anti_fluff,
integrity_guards, presentation_linter) that AUTO-FIX what they can.
This module runs AFTER scrubbers and SCANS for residual issues that
the scrubbers couldn't fix automatically — patterns we've seen recur
across builds where the LLM output is "wrong but not fixable by a
known rewrite rule."

When this module finds any hit it returns a list of issue strings.
The build pipeline marks the package as `quality_blocked: true` and
attaches the issue list to the package metadata, so the dashboard
can surface "review this build before applying" instead of silently
shipping a broken artifact.

Categories of checks:

1. **Coherence breaks** (from anti_fluff.find_coherence_breaks) —
   LLM word-drops like "supported user so" that produce broken
   sentences. Not auto-fixable because we don't know what word
   was dropped.

2. **Brand-casing escapes** — if the scrubber's brand list missed
   a variant, this catches the canonical bad-casing patterns we
   know about. Run on the FINAL output, so anything that survived
   the scrubber surfaces here.

3. **Format consistency** — if some date strings use "Sept" and
   others use "Sep" in the same artifact, that's drift; the
   scrubber normalizes both to "Sep" but if anything slips
   through we still want to know.

4. **Address-line sanity** — cover-letter "Hiring Team, X" where
   X still looks like a domain (contains "." and a known TLD).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_BAD_CASING_PATTERNS: List[tuple] = [
    # Canonical brand mis-casings the scrubber should have caught but
    # might miss in edge cases (different forms, inside parens, etc.).
    (r"\bICIMS\b", "iCIMS brand mis-casing (should be 'iCIMS')"),
    (r"\bIcims\b", "iCIMS brand mis-casing (should be 'iCIMS')"),
    (r"\bIos\b", "iOS brand mis-casing (should be 'iOS')"),
    (r"\bIphone\b", "iPhone brand mis-casing (should be 'iPhone')"),
    (r"\bGithub\b", "GitHub brand mis-casing (should be 'GitHub')"),
    (r"\bLinkedin\b", "LinkedIn brand mis-casing (should be 'LinkedIn')"),
    (r"\bKpi\b", "KPI acronym mis-casing (should be 'KPI')"),
    (r"\bApi\b", "API acronym mis-casing (should be 'API')"),
]

_BAD_DATE_PATTERNS: List[tuple] = [
    (r"\bSept\s+\d{4}\b", "non-canonical month 'Sept' (should be 'Sep')"),
    (r"\bOctb\s+\d{4}\b", "non-canonical month 'Octb' (should be 'Oct')"),
    (r"\bDecb\s+\d{4}\b", "non-canonical month 'Decb' (should be 'Dec')"),
]

# Known TLDs that, when they appear at the end of an address-line
# value, indicate the cover letter is still addressing a domain
# instead of a normalized company name.
_DOMAIN_TLD_PATTERN = re.compile(
    r"\b[\w-]+\.(?:com|net|org|io|co|ai|app|tech)\b",
    flags=re.IGNORECASE,
)


def check_resume_content(content: Dict[str, Any]) -> List[str]:
    """Scan a tailored resume JSON for known regression patterns.

    Returns a list of issue strings; empty list means clean.
    """
    from job_pipeline.anti_fluff import find_coherence_breaks

    issues: List[str] = []

    if not isinstance(content, dict):
        return issues

    # Pull every string field worth scanning into one buffer so we
    # don't repeat the pattern loops per-field.
    buffer_parts: List[str] = []
    for key in ("summary",):
        v = content.get(key)
        if isinstance(v, str) and v:
            buffer_parts.append(v)
    exps = content.get("experience")
    if isinstance(exps, list):
        for exp in exps:
            if not isinstance(exp, dict):
                continue
            for k in ("title", "company", "duration", "description"):
                v = exp.get(k)
                if isinstance(v, str) and v:
                    buffer_parts.append(v)
            bullets = exp.get("bullets")
            if isinstance(bullets, list):
                for b in bullets:
                    if isinstance(b, str) and b:
                        buffer_parts.append(b)
    skills = content.get("skills")
    if isinstance(skills, dict):
        for bucket in ("technical", "tools", "soft"):
            items = skills.get(bucket)
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, str) and it:
                        buffer_parts.append(it)
    projects = content.get("projects")
    if isinstance(projects, list):
        for proj in projects:
            if isinstance(proj, dict):
                for k in ("name", "description", "impact"):
                    v = proj.get(k)
                    if isinstance(v, str) and v:
                        buffer_parts.append(v)
    haystack = "\n".join(buffer_parts)

    issues.extend(find_coherence_breaks(haystack))

    for pattern, label in _BAD_CASING_PATTERNS:
        if re.search(pattern, haystack):
            issues.append(label)

    for pattern, label in _BAD_DATE_PATTERNS:
        if re.search(pattern, haystack):
            issues.append(label)

    return sorted(set(issues))


def check_cover_letter_content(content: Dict[str, Any]) -> List[str]:
    """Scan a cover-letter JSON for known regression patterns."""
    from job_pipeline.anti_fluff import find_coherence_breaks

    issues: List[str] = []
    if not isinstance(content, dict):
        return issues

    buffer_parts: List[str] = []
    for key in ("opening", "closing"):
        v = content.get(key)
        if isinstance(v, str) and v:
            buffer_parts.append(v)
    body = content.get("body_paragraphs")
    if isinstance(body, list):
        for p in body:
            if isinstance(p, str) and p:
                buffer_parts.append(p)
    # Address line for the "Hiring Team, <X>" sanity check.
    addr = content.get("address_line") or content.get("greeting") or ""
    if isinstance(addr, str) and addr:
        buffer_parts.append(addr)
        # Specifically scan the address for residual domain shape.
        if _DOMAIN_TLD_PATTERN.search(addr):
            issues.append(
                "cover letter address-line still contains a domain "
                "(company-name normalizer didn't run or didn't recognize the domain)"
            )

    haystack = "\n".join(buffer_parts)
    issues.extend(find_coherence_breaks(haystack))

    for pattern, label in _BAD_CASING_PATTERNS:
        if re.search(pattern, haystack):
            issues.append(label)

    for pattern, label in _BAD_DATE_PATTERNS:
        if re.search(pattern, haystack):
            issues.append(label)

    return sorted(set(issues))


def write_issue_log(
    item_id: int,
    *,
    resume_md_path: str = "",
    cover_letter_md_path: str = "",
    automated_issues: Optional[List[str]] = None,
    gate_revisions: int = 0,
    judge_score: Optional[float] = None,
    quality_block: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Persist a per-build issue log JSON next to the resume artifacts.

    Output path: <resume_md_dir>/<resume_md_stem>_issues.json

    Schema (versioned so format can evolve):
      {
        "schema_version": 1,
        "item_id": <int>,
        "build_timestamp": <ISO8601>,
        "artifacts": {"resume_md": ..., "cover_letter_md": ...},
        "automated_issues": [<str>, ...],         # from regression_check
        "manual_issues": [],                      # appended later via record_manual_issue
        "build_meta": {"gate_revisions": N, "judge_score": float, "quality_block": bool},
        "extra": {...}                            # arbitrary build-context bag
      }

    Returns the JSON path written, or None if nothing was persisted (e.g.
    no resume artifact to derive the path from).

    Idempotent: writing twice for the same build_timestamp overwrites.
    APPEND-ONLY across builds: every rebuild gets a NEW file because the
    artifact stem is the same but the file gets overwritten with fresh
    automated_issues. To preserve history across rebuilds, see
    `archive_issue_log()` below.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    if not resume_md_path:
        return None
    p = Path(resume_md_path)
    if not p.parent.is_dir():
        return None
    log_path = p.with_name(p.stem + "_issues.json")

    # Preserve any prior manual_issues / extra fields the user added before
    # this rebuild. Automated_issues always reflects this build's fresh run.
    prior_manual: List[Dict[str, Any]] = []
    prior_extra: Dict[str, Any] = {}
    if log_path.is_file():
        try:
            old = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                if isinstance(old.get("manual_issues"), list):
                    prior_manual = old["manual_issues"]
                if isinstance(old.get("extra"), dict):
                    prior_extra = old["extra"]
        except (OSError, json.JSONDecodeError):
            pass

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "item_id": item_id,
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            "resume_md": str(resume_md_path),
            "cover_letter_md": str(cover_letter_md_path) if cover_letter_md_path else "",
        },
        "automated_issues": list(automated_issues or []),
        "manual_issues": prior_manual,
        "build_meta": {
            "gate_revisions": int(gate_revisions),
            "judge_score": judge_score,
            "quality_block": bool(quality_block),
        },
        "extra": {**prior_extra, **(extra or {})},
    }
    try:
        log_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return str(log_path)
    except OSError:
        return None


def record_manual_issue(
    resume_md_path: str,
    *,
    severity: str,
    category: str,
    message: str,
    fix_hint: str = "",
) -> Optional[str]:
    """Append a manually-observed issue to the build's issue log.

    Use when a human review surfaces something the automated regression_check
    missed (e.g. tailoring weakness, AI-tells, tone drift). These accumulate
    across rebuilds (we never overwrite manual_issues) so the team has a
    durable record of what kept going wrong on a given item.

    Args:
        resume_md_path: path to the resume .md (same stem used for the log).
        severity: 'red' | 'yellow' | 'green'.
        category: short tag — 'coherence', 'tailoring', 'tone', 'format',
            'truth', 'casing', 'length', 'other'.
        message: one-line human description of the issue.
        fix_hint: optional suggested fix.

    Returns the log path written, or None on error.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    p = Path(resume_md_path)
    if not p.parent.is_dir():
        return None
    log_path = p.with_name(p.stem + "_issues.json")
    if not log_path.is_file():
        # Bootstrap an empty log so the manual issue isn't lost when the
        # log doesn't exist yet (e.g. caller hasn't run a build with
        # write_issue_log yet).
        log_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "item_id": 0,
                    "build_timestamp": datetime.now(timezone.utc).isoformat(),
                    "artifacts": {"resume_md": str(resume_md_path)},
                    "automated_issues": [],
                    "manual_issues": [],
                    "build_meta": {},
                    "extra": {},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    manual = data.get("manual_issues")
    if not isinstance(manual, list):
        manual = []
    manual.append(
        {
            "severity": severity.lower().strip(),
            "category": category.lower().strip(),
            "message": message.strip(),
            "fix_hint": fix_hint.strip(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    data["manual_issues"] = manual
    try:
        log_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return str(log_path)
    except OSError:
        return None


def query_recent_issues(
    artifacts_dir: str = "generated_resumes",
    *,
    limit: int = 20,
) -> Dict[str, Any]:
    """Walk recent issue logs and summarize recurring issues across builds.

    Returns a structured dict suitable for either CLI inspection or feeding
    into a downstream "prior failures" prompt block (future Phase 3D).

    Output shape:
      {
        "scanned": N,
        "total_automated_issues": N,
        "total_manual_issues": N,
        "by_message": {<message>: <count>, ...},  # most frequent first
        "recent_blocks": [<item_id>, ...],         # quality_block=true builds
      }
    """
    import json
    from collections import Counter
    from pathlib import Path

    out: Dict[str, Any] = {
        "scanned": 0,
        "total_automated_issues": 0,
        "total_manual_issues": 0,
        "by_message": {},
        "recent_blocks": [],
    }
    root = Path(artifacts_dir)
    if not root.is_dir():
        return out

    # Sort by mtime desc, take most recent `limit` issue logs.
    logs = sorted(
        root.glob("*_issues.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    counter: Counter = Counter()
    for log_path in logs:
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        out["scanned"] += 1
        for issue in data.get("automated_issues") or []:
            counter[str(issue)] += 1
            out["total_automated_issues"] += 1
        for issue in data.get("manual_issues") or []:
            if isinstance(issue, dict):
                counter[str(issue.get("message", ""))] += 1
            else:
                counter[str(issue)] += 1
            out["total_manual_issues"] += 1
        if (data.get("build_meta") or {}).get("quality_block"):
            iid = data.get("item_id")
            if iid:
                out["recent_blocks"].append(iid)

    out["by_message"] = dict(counter.most_common())
    return out


def check_artifact_files(
    resume_md_path: str = "",
    cover_letter_md_path: str = "",
) -> List[str]:
    """Scan rendered markdown artifacts. Used by the build pipeline
    after rendercv has produced the final files, so we catch anything
    that survived JSON-content scrubbing OR was introduced by export
    transformations.
    """
    from job_pipeline.anti_fluff import find_coherence_breaks

    issues: List[str] = []
    for path, label in (
        (resume_md_path, "resume"),
        (cover_letter_md_path, "cover letter"),
    ):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for coh in find_coherence_breaks(text):
            issues.append(f"{label}: {coh}")
        for pattern, lbl in _BAD_CASING_PATTERNS:
            if re.search(pattern, text):
                issues.append(f"{label}: {lbl}")
        for pattern, lbl in _BAD_DATE_PATTERNS:
            if re.search(pattern, text):
                issues.append(f"{label}: {lbl}")
    return sorted(set(issues))
