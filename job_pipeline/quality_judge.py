"""Calibrated presentation-quality judge — read-only, anchor-grounded, temp 0.

Scores subjective presentation quality (tone, persuasion, coherence, human polish)
AFTER deterministic linters run. Does NOT rewrite content and does NOT override
truth gates (evidence_db / truth_classifier).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.openai_client import OpenAIKeyMissingError, openai_generate_json_with_retry


logger = logging.getLogger(__name__)

_ANCHOR_FNAME_RE = re.compile(
    r"^(\d+)_(bad|mid|nearmiss|target|weak)_([\d.]+)_(.+)\.md$",
    re.IGNORECASE,
)

_TIER_WHY: Dict[str, str] = {
    "bad": "Kitchen-sink / template / unfocused / buzzword-heavy — presentation floor.",
    "weak": "Weak hook, repetition, little role fit — below submit bar.",
    "mid": "Keyword-stuffed or generic — usable but obviously generated.",
    "nearmiss": "Almost submit-ready but clear polish gaps — explicitly NOT a 9.",
    "target": "Clean, targeted, human-polished — this IS the 9 bar for this role family.",
}

_judge_cache: Dict[str, Dict[str, Any]] = {}
_anchors_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None


@dataclass
class JudgeAnchor:
    doc_type: str
    tier: str
    score: float
    slug: str
    excerpt: str
    filename: str
    why: str
    job_context: str = ""
    source_url: str = ""


def _extract_job_context(rationale: Optional[Dict[str, Any]], *, limit: int = 2500) -> Tuple[str, str]:
    """Return (job_context_text, source_url) from a rationale sidecar."""
    if not rationale:
        return "", ""
    url = str(rationale.get("source_url") or "").strip()
    posting = str(rationale.get("job_posting") or "").strip()
    jd_ctx = str(rationale.get("jd_context") or "").strip()
    body = posting or jd_ctx
    if len(body) > limit:
        body = body[:limit] + "\n…[job posting truncated]"
    return body, url


def judge_enabled() -> bool:
    """Default ON when anchors exist. Disable with RESUME_OPT_JUDGE=0."""
    if os.getenv("RESUME_OPT_JUDGE", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    anchors = load_judge_anchors()
    return bool(anchors.get("resume") or anchors.get("cover_letter"))


def pkg_judge_enabled() -> bool:
    """Holistic resume+CL judge after both docs are built. Default OFF — resume gate judge is enough for most builds."""
    if os.getenv("RESUME_OPT_PKG_JUDGE", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    return judge_enabled()


def opt_judge_min() -> int:
    raw = (os.getenv("RESUME_OPT_JUDGE_MIN") or "88").strip()
    try:
        return max(0, min(100, int(raw)))
    except ValueError:
        return 88


def judge_model() -> str:
    return (
        os.getenv("RESUME_JUDGE_MODEL")
        or os.getenv("OPENAI_CRITIQUE_MODEL")
        or "gpt-4.1-mini"
    ).strip()


def _anchors_root() -> Path:
    env = (os.getenv("JUDGE_ANCHORS_DIR") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "judge_anchors"


def _rationale_sidecar_path(anchor_md: Path) -> Path:
    return anchor_md.with_suffix(".rationale.json")


def _load_rationale_sidecar(anchor_md: Path) -> Optional[Dict[str, Any]]:
    path = _rationale_sidecar_path(anchor_md)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("quality_judge: could not read rationale sidecar %s (%s)", path, exc)
        return None
    return raw if isinstance(raw, dict) else None


def _compose_anchor_why(tier: str, score: float, rationale: Optional[Dict[str, Any]]) -> str:
    """Build prompt 'why' from sidecar observations; fall back to generic tier label."""
    base = _TIER_WHY.get(tier, f"Calibration anchor tier={tier} score≈{score}")
    if not rationale:
        return base

    parts: List[str] = []
    tier_match = str(rationale.get("tier_definition_match") or "").strip()
    if tier_match:
        parts.append(tier_match)

    rat = rationale.get("rationale") if isinstance(rationale.get("rationale"), dict) else {}
    judged_by = rationale.get("judged_by")
    if isinstance(judged_by, list):
        for engine in judged_by:
            key = str(engine).strip().lower()
            text = str(rat.get(key) or rat.get(engine) or "").strip()
            if text:
                parts.append(f"{key}: {text}")
    elif isinstance(rat, dict):
        for key, text in rat.items():
            if text:
                parts.append(f"{key}: {str(text).strip()}")

    strengths = rationale.get("key_strengths")
    if isinstance(strengths, list) and strengths:
        parts.append(
            "Strengths in this example: "
            + "; ".join(str(s).strip() for s in strengths[:4] if str(s).strip())
        )

    weaknesses = rationale.get("key_weaknesses")
    if isinstance(weaknesses, list) and weaknesses:
        parts.append(
            "Weaknesses in this example: "
            + "; ".join(str(w).strip() for w in weaknesses[:5] if str(w).strip())
        )

    company = str(rationale.get("target_company") or "").strip()
    role = str(rationale.get("target_role") or "").strip()
    scores = rationale.get("scores")
    if isinstance(scores, dict):
        score_bits = [
            f"{k}: {v}/10"
            for k, v in scores.items()
            if v is not None and str(v).strip()
        ]
        if score_bits:
            parts.append("Scores: " + ", ".join(score_bits))

    score_scope = rationale.get("score_scope")
    if isinstance(score_scope, dict):
        scope_bits = [
            f"{k} ({v})"
            for k, v in score_scope.items()
            if v is not None and str(v).strip()
        ]
        if scope_bits:
            parts.append("Score scope: " + "; ".join(scope_bits))

    if company or role:
        parts.append(f"Tailored for: {company} — {role}".strip(" —"))

    return "\n".join(parts) if parts else base


def _parse_anchor_file(path: Path, doc_type: str) -> Optional[JudgeAnchor]:
    m = _ANCHOR_FNAME_RE.match(path.name)
    if not m:
        return None
    tier = m.group(2).lower()
    try:
        score = float(m.group(3))
    except ValueError:
        score = 0.0
    slug = m.group(4)
    try:
        excerpt = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if len(excerpt) > 4500:
        excerpt = excerpt[:4500] + "\n…[truncated]"
    rationale = _load_rationale_sidecar(path)
    job_context, source_url = _extract_job_context(rationale)
    return JudgeAnchor(
        doc_type=doc_type,
        tier=tier,
        score=score,
        slug=slug,
        excerpt=excerpt,
        filename=path.name,
        why=_compose_anchor_why(tier, score, rationale),
        job_context=job_context,
        source_url=source_url,
    )


def load_judge_anchors(*, force_reload: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Load resume + cover-letter anchor excerpts from judge_anchors/."""
    global _anchors_cache
    if _anchors_cache is not None and not force_reload:
        return _anchors_cache

    root = _anchors_root()
    out: Dict[str, List[Dict[str, Any]]] = {"resume": [], "cover_letter": []}
    for sub, key in (("resumes", "resume"), ("cover_letters", "cover_letter")):
        folder = root / sub
        if not folder.is_dir():
            continue
        anchors: List[JudgeAnchor] = []
        for path in sorted(folder.glob("*.md")):
            a = _parse_anchor_file(path, key)
            if a:
                anchors.append(a)
        anchors.sort(key=lambda a: a.score)
        out[key] = [
            {
                "tier": a.tier,
                "score": a.score,
                "slug": a.slug,
                "why": a.why,
                "excerpt": a.excerpt,
                "filename": a.filename,
                "job_context": a.job_context,
                "source_url": a.source_url,
            }
            for a in anchors
        ]

    _anchors_cache = out
    return out


def _anchors_prompt_block(anchors: Dict[str, List[Dict[str, Any]]]) -> str:
    parts: List[str] = [
        "CALIBRATION ANCHORS — use these to anchor your 0–100 presentation scale.",
        "Score the DRAFT relative to these real examples. Do NOT re-litigate objective "
        "defects (capitalization, banned phrases) — assume linters already ran.",
        "",
    ]
    for label, key in (("RESUME", "resume"), ("COVER LETTER", "cover_letter")):
        items = anchors.get(key) or []
        if not items:
            continue
        parts.append(f"=== {label} ANCHORS ===")
        for a in items:
            block = (
                f"--- {a['tier'].upper()} (~{a['score']}/10 presentation) — {a['slug']} ---\n"
                f"Why: {a['why']}\n"
            )
            if a.get("source_url"):
                block += f"Source: {a['source_url']}\n"
            if a.get("job_context"):
                block += f"Job posting (this example was tailored for):\n{a['job_context']}\n"
            block += f"{a['excerpt']}\n"
            parts.append(block)
    return "\n".join(parts)


def _content_fingerprint(
    resume_content: Dict[str, Any],
    cover_letter_content: Optional[Dict[str, Any]],
) -> str:
    blob = json.dumps(
        {"r": resume_content, "c": cover_letter_content},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _judge_system() -> str:
    return (
        "You are a calibrated presentation-quality judge for job application packages. "
        "You score ONLY subjective presentation: compelling tone, JD relevance, narrative "
        "coherence, human polish, cover-letter persuasion. "
        "Truthfulness is handled elsewhere — do not reward fabrication, but you may note "
        "suspected overclaims in critique without changing the score much. "
        "Do NOT re-score objective linter items (skill casing, banned phrases, semicolons). "
        "Use the CALIBRATION ANCHORS to anchor scores: "
        "bad≈3, mid≈6–7.5, nearmiss≈8, target≈9+. "
        "A nearmiss is explicitly NOT a 9. Return exactly one JSON object, no markdown."
    )


def _judge_user(
    resume_content: Dict[str, Any],
    *,
    job_description: str,
    job_title: str,
    cover_letter_content: Optional[Dict[str, Any]],
    anchors: Dict[str, List[Dict[str, Any]]],
) -> str:
    cl_block = ""
    if cover_letter_content:
        cl_block = (
            "\nCOVER LETTER JSON:\n"
            + json.dumps(cover_letter_content, ensure_ascii=False)[:6000]
        )
    return (
        _anchors_prompt_block(anchors)
        + f"\n\nTARGET ROLE: {job_title or '(unknown)'}\n"
        f"JD (context, first 2500 chars):\n{(job_description or '')[:2500]}\n\n"
        f"RESUME JSON TO SCORE:\n{json.dumps(resume_content, ensure_ascii=False)[:8000]}"
        + cl_block
        + "\n\nReturn EXACTLY this JSON shape:\n"
        "{\n"
        '  "score": <0-100 overall presentation, integer>,\n'
        '  "resume_score": <0-100 or null>,\n'
        '  "cover_letter_score": <0-100 or null>,\n'
        '  "subscores": {"compelling": 1-10, "relevance": 1-10, "tone": 1-10, "coherence": 1-10},\n'
        '  "critique": ["specific fix 1", "specific fix 2", ...],\n'
        '  "verdict": "<nearest anchor tier: bad|weak|mid|nearmiss|target>"\n'
        "}\n"
        "score 88+ only when close to TARGET anchors on presentation. "
        "nearmiss-quality drafts should land ~78–84, not 90+."
    )


def judge_quality(
    resume_content: Dict[str, Any],
    *,
    job_description: str = "",
    job_title: str = "",
    cover_letter_content: Optional[Dict[str, Any]] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Read-only calibrated judge. Returns {ok, score, critique, ...} or {ok: False}."""
    if not isinstance(resume_content, dict) or resume_content.get("error"):
        return {"ok": False, "reason": "invalid resume content"}

    if not judge_enabled():
        return {"ok": False, "reason": "judge disabled or no anchors"}

    fp = _content_fingerprint(resume_content, cover_letter_content)
    if use_cache and fp in _judge_cache:
        return dict(_judge_cache[fp])

    anchors = load_judge_anchors()
    if not (anchors.get("resume") or anchors.get("cover_letter")):
        return {"ok": False, "reason": "no anchors loaded"}

    try:
        raw = openai_generate_json_with_retry(
            model=judge_model(),
            system=_judge_system(),
            user=_judge_user(
                resume_content,
                job_description=job_description,
                job_title=job_title,
                cover_letter_content=cover_letter_content,
                anchors=anchors,
            ),
            label="quality_judge",
            temperature=0.0,
        )
    except OpenAIKeyMissingError as exc:
        logger.warning("quality_judge: %s", exc)
        return {"ok": False, "reason": "openai key missing"}
    except Exception as exc:
        logger.warning("quality_judge failed: %s", exc)
        return {"ok": False, "reason": str(exc)}

    if not isinstance(raw, dict):
        return {"ok": False, "reason": "invalid judge response"}

    try:
        score = int(raw.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    result = {
        "ok": True,
        "score": score,
        "resume_score": raw.get("resume_score"),
        "cover_letter_score": raw.get("cover_letter_score"),
        "subscores": raw.get("subscores") if isinstance(raw.get("subscores"), dict) else {},
        "critique": [str(c) for c in (raw.get("critique") or []) if c][:8],
        "verdict": str(raw.get("verdict") or ""),
        "model": judge_model(),
        "judge_min": opt_judge_min(),
        "passes_gate": score >= opt_judge_min(),
    }
    if use_cache:
        _judge_cache[fp] = dict(result)
    return result


def judge_markdown_anchor(anchor: Dict[str, Any], *, doc_type: str) -> Dict[str, Any]:
    """Score a raw markdown anchor excerpt (for calibration eval)."""
    if not judge_enabled():
        return {"ok": False, "reason": "judge disabled"}
    fake_resume = {"summary": anchor.get("excerpt", ""), "experience": [], "skills": {}}
    fake_cl = None
    if doc_type == "cover_letter":
        fake_cl = {"opening": anchor.get("excerpt", ""), "body_paragraphs": [], "closing": ""}
        fake_resume = {"summary": "(cover letter anchor — resume not scored)", "experience": [], "skills": {}}
    return judge_quality(
        fake_resume,
        job_description="",
        job_title=anchor.get("slug", ""),
        cover_letter_content=fake_cl,
        use_cache=False,
    )


def clear_judge_cache() -> None:
    _judge_cache.clear()
