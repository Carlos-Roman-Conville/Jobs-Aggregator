"""Heuristic ATS-style overlap between resume corpus text and JD (no vendor APIs)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Sequence, Tuple

def load_application_assets() -> str:
    return "{}"

logger = logging.getLogger(__name__)

_MIN_CANONICAL_CHARS = 120


_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "your",
        "you",
        "our",
        "are",
        "will",
        "have",
        "has",
        "must",
        "may",
        "not",
        "all",
        "any",
        "can",
        "such",
        "into",
        "about",
        "their",
        "they",
        "what",
        "when",
        "who",
        "how",
        "was",
        "were",
        "been",
        "being",
        "also",
        "more",
        "some",
        "than",
        "then",
        "there",
        "here",
        "each",
        "other",
        "using",
        "use",
        "used",
        "including",
        "include",
        "required",
        "preferred",
        "years",
        "year",
        "experience",
        "team",
        "work",
        "role",
        "job",
        "company",
        "skills",
        "ability",
        "strong",
        "excellent",
    }
)


def _tokens(text: str) -> List[str]:
    raw = re.findall(r"[a-z0-9#+/]{2,}", (text or "").lower())
    out: List[str] = []
    for t in raw:
        if t in _STOP or t.isdigit() and len(t) > 3:
            continue
        out.append(t)
    return out


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _overlap_coeff(a: Sequence[str], b: Sequence[str]) -> float:
    """Size-robust overlap: |intersection| / |smaller set|.

    Jaccard collapses toward zero when one document (the resume blob) is much
    shorter than the other (a long JD), so it is a poor ATS proxy. The overlap
    coefficient normalizes by the smaller vocabulary, i.e. "what fraction of the
    resume's terms appear in the JD" — which is what an ATS keyword pass cares
    about — and stays meaningful regardless of JD length.
    """
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / min(len(sa), len(sb))


# Generic qualifier words stripped from skill phrases before matching, so e.g.
# "active directory basics" can match "Active Directory" in a JD.
_SKILL_QUALIFIER_WORDS = frozenset({"basics", "basic", "supporting", "general", "optional"})


def _stem(w: str) -> str:
    """Very light suffix stripper for forgiving word-level matching."""
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _normalize_skill(s: str) -> str:
    low = re.sub(r"\([^)]*\)", " ", str(s).lower())  # drop parenthetical qualifiers
    low = re.sub(r"[^a-z0-9#+/ ]+", " ", low)         # keep alnum + a few tech chars
    low = re.sub(r"\s+", " ", low).strip()
    words = [w for w in low.split(" ") if w and w not in _SKILL_QUALIFIER_WORDS]
    return " ".join(words)


def _skill_variants(skill_norm: str) -> List[str]:
    """Phrase plus a singular/plural toggle of the final word."""
    if not skill_norm:
        return []
    variants = {skill_norm}
    words = skill_norm.split(" ")
    last = words[-1]
    if last.endswith("s") and len(last) > 3:
        variants.add(" ".join(words[:-1] + [last[:-1]]))
    else:
        variants.add(" ".join(words[:-1] + [last + "s"]))
    return [v for v in variants if v]


def build_canonical_resume_text(
    *,
    extra_paths_text: List[str] | None = None,
) -> Tuple[str, str]:
    """Return (combined_text_for_scoring, source_note)."""
    parts: List[str] = []
    note_bits: List[str] = []

    try:
        from job_pipeline.bootstrap_resume_profile import (
            load_consolidated_profile,
            load_consolidated_profile_text,
        )

        prof_md = (load_consolidated_profile_text() or "").strip()
        if prof_md:
            parts.append(prof_md[:18000])
            note_bits.append("consolidated_profile.md")

        prof = load_consolidated_profile()
        if isinstance(prof, dict):
            headline = str(prof.get("headline") or "").strip()
            if headline:
                parts.append(headline)
            summary = str(prof.get("summary") or "").strip()
            if summary:
                parts.append(summary)
            sk = prof.get("skills")
            if isinstance(sk, dict):
                for key in ("technical", "soft"):
                    items = sk.get(key)
                    if isinstance(items, list):
                        parts.append(" ".join(str(x) for x in items if x))
            elif isinstance(sk, list):
                parts.append(" ".join(str(x) for x in sk if x))
            exps = prof.get("experience")
            if isinstance(exps, list):
                for exp in exps[:8]:
                    if not isinstance(exp, dict):
                        continue
                    for field in ("title", "company", "summary"):
                        val = str(exp.get(field) or "").strip()
                        if val:
                            parts.append(val)
                    bullets = exp.get("bullets")
                    if isinstance(bullets, list):
                        parts.extend(str(b).strip() for b in bullets[:6] if str(b).strip())
            if prof.get("name"):
                note_bits.append("consolidated_profile.json")
    except Exception as exc:
        logger.warning("build_canonical_resume_text: consolidated profile load failed: %s", exc)

    try:
        assets = json.loads(load_application_assets())
    except Exception:
        assets = {}

    resumes = assets.get("resumes") if isinstance(assets.get("resumes"), list) else []
    r: Dict[str, Any]
    for r in resumes:
        if not isinstance(r, dict):
            continue
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        nid = str(r.get("id") or "")
        if meta.get("headline"):
            parts.append(str(meta["headline"]))
        ks = meta.get("key_skills")
        if isinstance(ks, list):
            parts.append(" ".join(str(x) for x in ks if x))
        if meta.get("summary"):
            parts.append(str(meta["summary"]))
        if nid:
            note_bits.append(nid or "resume")

    for blob in extra_paths_text or []:
        if blob and blob.strip():
            parts.append(blob[:12000])

    combined = normalize_whitespace(" \n ".join(parts))
    note = "+".join([x for x in note_bits if x])[:200] if note_bits else "none"
    if len(combined) < _MIN_CANONICAL_CHARS:
        logger.warning(
            "build_canonical_resume_text: thin canonical resume text (%s chars); "
            "ATS overlap will be skipped. Check consolidated_profile.md and application_assets.",
            len(combined),
        )
        note = f"thin_profile:{len(combined)}chars"
    return combined, note


def normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def keyword_hit_ratio(jd_blob_lower: str, skill_terms: Sequence[str]) -> Tuple[float, List[str]]:
    hits: List[str] = []
    if not jd_blob_lower or not skill_terms:
        return 0.0, hits
    body = jd_blob_lower.replace("‑", "-")
    body_squashed = body.replace(" ", "")
    body_words = set(re.findall(r"[a-z0-9#+/]{2,}", body))
    body_stems = {_stem(w) for w in body_words}

    # Normalize + dedupe the skill list (drop parenthetical/qualifier noise).
    normalized: List[str] = []
    seen_norm = set()
    for s in skill_terms:
        n = _normalize_skill(s)
        if len(n) >= 2 and n not in seen_norm:
            normalized.append(n)
            seen_norm.add(n)
    if not normalized:
        return 0.0, hits

    capped = max(1, min(24, len(normalized)))
    for n in normalized:
        matched = False
        # 1) phrase match (plural/singular tolerant, whitespace-insensitive)
        for v in _skill_variants(n):
            if v in body or v.replace(" ", "") in body_squashed:
                matched = True
                break
        # 2) multi-word fallback: every content word present (stemmed) anywhere
        if not matched and " " in n:
            content = [w for w in n.split(" ") if len(w) >= 4]
            if content and all(_stem(w) in body_stems for w in content):
                matched = True
        if matched:
            hits.append(n)
    ratio = round(len(set(hits)) / capped, 4)
    return min(1.0, ratio), hits


def compute_ats_overlap(
    job_description: str,
    *,
    canonical_resume_blob: str,
    resume_skill_terms: Sequence[str],
) -> Dict[str, Any]:
    """Return ats_score 0..1 and notes dict."""
    blob = (canonical_resume_blob or "").strip()
    if len(blob) < _MIN_CANONICAL_CHARS:
        return {
            "ats_score": None,
            "ats_skipped": True,
            "ats_skip_reason": "thin_or_missing_profile",
            "ats_resume_chars": len(blob),
            "ats_overlap_coeff": 0.0,
            "ats_jaccard": 0.0,
            "ats_keyword_ratio": 0.0,
            "ats_keyword_hits": [],
        }
    rtoks = _tokens(blob)
    dtoks = _tokens(job_description)
    overlap = _overlap_coeff(rtoks, dtoks)
    jac = _jaccard(rtoks, dtoks)  # retained for diagnostics only
    jd_low = (job_description or "").lower()
    kratio, khits = keyword_hit_ratio(jd_low, resume_skill_terms)
    # Blend size-robust lexical overlap with intentional skill harvesting.
    score = 0.5 * overlap + 0.5 * min(1.0, kratio * 1.25)
    score = round(max(0.0, min(1.0, score)), 4)
    return {
        "ats_score": score,
        "ats_overlap_coeff": round(overlap, 4),
        "ats_jaccard": round(jac, 4),
        "ats_keyword_ratio": kratio,
        "ats_keyword_hits": khits[:18],
        "ats_resume_chars": len(canonical_resume_blob),
    }


def extract_min_years_experience(job_description: str) -> Tuple[int | None, str]:
    """
    Lower-bound extraction for JD year requirements.

    Strict patterns (taken FIRST and the MAX across matches wins) include:
      - "N-M years of hands-on / relevant / professional / directly related"
      - "minimum N years" / "at least N years"
      - "N+ years required"

    These dominate the result because they signal a hard minimum tied to the
    actual job scope. If no strict pattern matches, falls back to loose
    patterns (bare "N+ years" / "N-M years" / "N years of experience").

    Why we don't always take the first hit: JDs commonly include a soft
    line like "Bachelor's degree or 3+ years of experience" AND a stricter
    line like "4-6 years of hands-on support". The stricter line is what
    actually screens candidates — taking the loose one undercounts the JD's
    real minimum.

    Returns (years_or_none, match_span_note).
    """
    d = (job_description or "").lower()
    strict_patterns = [
        # "4-6 years of hands-on / relevant / professional / directly related / specific"
        (
            r"\b(\d{1,2})(?:\s*-\s*\d{1,2})?\s*\+?\s*years?\s+of\s+"
            r"(?:hands-on|relevant|professional|directly\s+related|specific|"
            r"prior|equivalent|qualifying|substantive|applied|demonstrated)",
            "of_hands_on",
        ),
        # "minimum X years" / "min. X years" / "at least X years"
        (
            r"\b(?:minimum\s+(?:of\s+)?|min\.?\s+|at\s+least\s+)(\d{1,2})\+?\s*years?",
            "minimum",
        ),
        # "X+ years required" / "X years required" / "X+ years must have"
        (
            r"\b(\d{1,2})\+?\s*years?\s+(?:required|must\s+have)\b",
            "required",
        ),
        # "X years experience required" / "X years of experience minimum"
        (
            r"\b(\d{1,2})\+?\s*years?\s+(?:of\s+)?experience\s+(?:required|needed|minimum|must)",
            "experience_required",
        ),
    ]
    strict_hits: List[Tuple[int, str]] = []
    for pat, label in strict_patterns:
        for m in re.finditer(pat, d):
            try:
                strict_hits.append((int(m.group(1)), f"{label}:{m.group(0)[:48]}"))
            except (ValueError, IndexError):
                continue
    if strict_hits:
        best = max(strict_hits, key=lambda x: x[0])
        return best

    # Loose fallback (legacy behavior).
    m = re.search(r"\b(\d{1,2})\+\s*years\b", d)
    if m:
        return int(m.group(1)), m.group(0)
    m2 = re.search(r"\b(\d{1,2})\s*-\s*\d{1,2}\s*years\b", d)
    if m2:
        return int(m2.group(1)), m2.group(0)
    m3 = re.search(r"\b(\d{1,2})\s*years(?:\s+of)?\s*(?:experience|exp)\b", d)
    if m3:
        return int(m3.group(1)), m3.group(0)
    return None, ""
