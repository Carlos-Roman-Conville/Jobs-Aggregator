import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

SummarizeProgressFn = Callable[[int, int, int, bool, str], None]

from openai import OpenAI

from job_pipeline.openai_client import is_temperature_parameter_error, resolve_openai_temperature

# After search_preferences multipliers, unclamped product is stored on the card as
# ``fit_score_raw``; ``fit_score_blended`` stays in [0, 1] for legacy consumers.

# Written on every successful summarize; bump order in _PROMPT_FRAMING_VERSION_ORDER when adding versions.
PROMPT_FRAMING_VERSION = "v2-it-first-2026-05"
_PROMPT_FRAMING_VERSION_ORDER: Tuple[str, ...] = ("v2-it-first-2026-05",)

if PROMPT_FRAMING_VERSION not in _PROMPT_FRAMING_VERSION_ORDER:
    raise RuntimeError(
        "PROMPT_FRAMING_VERSION must be listed in _PROMPT_FRAMING_VERSION_ORDER "
        "(append older ids before bumping the constant)."
    )

from job_pipeline.bootstrap_resume_profile import load_consolidated_profile_text
from job_pipeline.db import count_items_by_status, get_item, list_items_by_statuses, set_item_summary
from job_pipeline.ingest import load_pipeline_config, matching_thresholds, salary_hard_gate
from job_pipeline.ats_score import build_canonical_resume_text, compute_ats_overlap, extract_min_years_experience
from job_pipeline.domain_fit import (
    calculate_domain_fit,
    career_identity_prompt_block,
    load_career_profile,
    merge_blended_with_domain,
)
from job_pipeline.location_policy import evaluate_location_policy
from job_pipeline.search_preferences import score_posting_against_preferences


def parse_summary_json(raw: Any) -> Dict[str, Any]:
    """Best-effort parse of summary_json from DB (dict or JSON string)."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            o = json.loads(raw)
            return dict(o) if isinstance(o, dict) else {}
        except Exception:
            return {}
    return {}


def summary_prompt_framing_is_stale(raw_summary: Any) -> bool:
    """
    True if this row needs a fresh LLM summarize for prompt/metadata framing.

    Uses ``prompt_framing_version`` on the summary dict only — no DB migrations.
    Missing version or any version older than ``PROMPT_FRAMING_VERSION`` (per
    ``_PROMPT_FRAMING_VERSION_ORDER``) is stale; unknown strings are treated as stale.
    """
    summary = parse_summary_json(raw_summary)
    rec = summary.get("prompt_framing_version")
    if rec is None:
        return True
    rs = str(rec).strip()
    if not rs:
        return True
    if rs == PROMPT_FRAMING_VERSION:
        return False
    if rs not in _PROMPT_FRAMING_VERSION_ORDER:
        return True
    try:
        return _PROMPT_FRAMING_VERSION_ORDER.index(rs) < _PROMPT_FRAMING_VERSION_ORDER.index(
            PROMPT_FRAMING_VERSION
        )
    except ValueError:
        return True


def search_preferences_filter_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read filters.search_preferences toggles from job_pipeline_config."""
    f = cfg.get("filters") if isinstance(cfg.get("filters"), dict) else {}
    sp = f.get("search_preferences") if isinstance(f.get("search_preferences"), dict) else {}
    return {
        "enabled": bool(sp.get("enabled", True)),
        "honor_auto_close": bool(sp.get("honor_auto_close", True)),
        "apply_multiplier": bool(sp.get("apply_multiplier", True)),
    }


def apply_search_preferences_stage(
    cfg: Dict[str, Any],
    *,
    title: str,
    description_text: str,
    location: str,
    salary_text: str,
    source: str,
    combined_after_location: float,
    loc_reject: bool,
) -> Tuple[float, Dict[str, Any], bool, Optional[str], float]:
    """
    Apply deterministic rules from search_preferences.md after the location-policy stage.

    Returns combined_after_preferences (clamped to 0..1 for legacy compatibility),
    card-facing dict, pref hard-close flag, auto_close code, and fit_score_raw
    (product before the 1.0 display clamp) for list_rank / tie-breaks.
    """
    toggles = search_preferences_filter_settings(cfg)
    posting = {
        "title": title,
        "description_text": description_text,
        "location": location,
        "salary_text": salary_text,
        "source": source,
    }

    if not toggles["enabled"]:
        neutral = {
            "pref_multiplier": 1.0,
            "auto_close_reason": None,
            "reject": False,
            "boost_signals": [],
            "preference_notes": ["Search preferences stage disabled via job_pipeline_config.json"],
            "work_mode": "unknown",
            "distance_miles_from_19107": None,
            "salary_low_usd": None,
            "salary_floor_applied": 0,
            "config_disabled": True,
            "effective_multiplier_applied": 1.0,
            "honor_auto_close": toggles["honor_auto_close"],
            "apply_multiplier": toggles["apply_multiplier"],
        }
        return combined_after_location, neutral, False, None, round(float(combined_after_location), 4)

    pref_res = score_posting_against_preferences(posting)
    raw_reason = pref_res.get("auto_close_reason")
    raw_mult = float(pref_res.get("pref_multiplier") or 1.0)

    honor_close = toggles["honor_auto_close"]
    pref_hard_close = bool(raw_reason) and honor_close

    apply_mult = toggles["apply_multiplier"]
    effective_mult = raw_mult if apply_mult else 1.0

    if pref_hard_close or loc_reject:
        fr = round(float(combined_after_location), 4)
        combined_after_preferences = round(min(1.0, max(0.0, fr)), 3)
        fit_raw = fr
    else:
        fit_raw = round(max(0.0, combined_after_location * effective_mult), 4)
        combined_after_preferences = round(min(1.0, fit_raw), 3)

    notes = list(pref_res.get("preference_notes") or [])
    if raw_reason and not honor_close:
        notes.append(f"(would_auto_close:{raw_reason}; honor_auto_close=false)")

    card = {
        "pref_multiplier": raw_mult,
        "auto_close_reason": raw_reason,
        "reject": pref_hard_close,
        "boost_signals": pref_res.get("boost_signals") or [],
        "preference_notes": notes,
        "work_mode": pref_res.get("work_mode"),
        "distance_miles_from_19107": pref_res.get("distance_miles_from_19107"),
        "salary_low_usd": pref_res.get("salary_low_usd"),
        "salary_floor_applied": pref_res.get("salary_floor_applied"),
        "effective_multiplier_applied": effective_mult,
        "honor_auto_close": honor_close,
        "apply_multiplier": apply_mult,
    }
    code = str(raw_reason) if raw_reason else None
    return combined_after_preferences, card, pref_hard_close, code, fit_raw


def _heuristic_fit(description: str, skills: List[str]) -> Tuple[float, int]:
    d = (description or "").lower()
    if not d:
        return 0.2, 0
    hits = 0
    for s in skills:
        if s and str(s).lower() in d:
            hits += 1
    return min(1.0, 0.2 + 0.12 * hits), hits


def _heuristic_junk(title: str, desc: str, company: str) -> Tuple[bool, str]:
    blob = f"{title} {desc} {company}".lower()
    phrases = (
        "talent community",
        "talent network",
        "future opportunity",
        "general interest",
        "stay in our pipeline",
        "general application",
        "spontaneous application",
        "express interest",
        "not a specific role",
        "no current openings",
    )
    for p in phrases:
        if p in blob:
            return True, f"listing_pattern:{p}"
    if len((desc or "").strip()) < 55 and "requirement" not in blob and "qualification" not in blob:
        return True, "thin_or_vague_posting"
    return False, ""


def _parse_json_from_model(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
        if m:
            t = m.group(1).strip()
    return json.loads(t)


def _compute_list_rank(rank_fit: float, verdict: str, junk: bool) -> float:
    """Verdict-weighted rank key. ``rank_fit`` is typically ``fit_score_raw`` (can exceed 1.0)."""
    if junk:
        return -1.0
    mult = {"strong_match": 1.14, "maybe": 0.93, "pass": 0.38}.get(verdict, 0.96)
    e = max(0.0, float(rank_fit))
    if e <= 1.0 + 1e-9:
        intensity = min(1.0, e)
    else:
        intensity = 1.0 + (e - 1.0)
    return round(intensity * mult, 4)


def _quality_bucket(verdict: str, junk: bool) -> str:
    if junk:
        return "junk"
    if verdict == "strong_match":
        return "strong"
    if verdict == "pass":
        return "weak"
    return "ok"


def _verdict_score_thresholds() -> Dict[str, float]:
    """Numeric floors a verdict must clear to be ratified.

    Defaults chosen so a "Strong" label requires BOTH a defensible blended fit
    AND a non-trivial ATS overlap. Tunable via env without redeploying code.
    """
    return {
        "strong_min_blended": float(os.getenv("VERDICT_STRONG_MIN_BLENDED") or 0.65),
        "strong_min_ats": float(os.getenv("VERDICT_STRONG_MIN_ATS") or 0.50),
        "pass_below_blended": float(os.getenv("VERDICT_PASS_BELOW_BLENDED") or 0.30),
    }


def _evaluate_truth_limit_fit(
    description: str,
    profile_text: str,
) -> Dict[str, Any]:
    """Classify JD requirements vs evidence.json truth_limits.

    Returns a dict describing how many JD-named requirements the candidate
    structurally cannot claim. Used to penalize jobs whose required skills
    are on the candidate's hard no-list (e.g. AD admin, M365 admin).

    Multiplier:
      - Each requirement BLOCKED by truth_limits costs 25%.
      - Each requirement NOT_TRUE for other reasons (no evidence) costs 8%.
      - Floored at 0.05 so the score can drop dramatically but never zero
        out (the user may still want to see the row).
    """
    try:
        from job_pipeline.truth_classifier import classify_jd_requirements
    except Exception:
        return {
            "ok": False,
            "multiplier": 1.0,
            "blocked": [],
            "not_true": [],
            "direct": [],
        }
    try:
        classifications = classify_jd_requirements(description or "", profile_text or "")
    except Exception:
        return {
            "ok": False,
            "multiplier": 1.0,
            "blocked": [],
            "not_true": [],
            "direct": [],
        }

    blocked: List[str] = []
    not_true: List[str] = []
    direct: List[str] = []
    for c in classifications:
        if not isinstance(c, dict):
            continue
        level = str(c.get("level") or "")
        label = str(c.get("label") or c.get("id") or "")
        if not label:
            continue
        if level == "not_true":
            reason = str(c.get("reason") or "")
            if "truth_limits" in reason or "blocked" in reason:
                blocked.append(label)
            else:
                not_true.append(label)
        elif level == "direct_proven":
            direct.append(label)

    blocked_n = len(blocked)
    not_true_n = len(not_true)
    raw_mult = 1.0 - (0.25 * blocked_n) - (0.08 * not_true_n)
    multiplier = max(0.05, round(raw_mult, 4))
    return {
        "ok": True,
        "multiplier": multiplier,
        "blocked": blocked,
        "not_true": not_true,
        "direct": direct,
    }


def _reconcile_verdict_with_scores(
    verdict: str,
    blended: float,
    ats_score: float,
) -> Tuple[str, str]:
    """Downgrade a verdict that the numeric scores don't actually support.

    The summarizer LLM overrates fit roughly 25% of the time (see backfill
    audit). A 35%-ATS / 62%-fit row labeled "strong_match" misleads both the
    dashboard's ranking and the user's review priorities. This deterministic
    reconciliation runs after the LLM's verdict comes back and downgrades it
    when the underlying numbers don't clear the floors.

    Returns (new_verdict, reason). Reason is empty string when no change.
    """
    th = _verdict_score_thresholds()
    if verdict == "strong_match":
        if blended < th["strong_min_blended"] or ats_score < th["strong_min_ats"]:
            return "maybe", (
                f"downgraded strong_match -> maybe "
                f"(blended={blended:.2f}<{th['strong_min_blended']:.2f} or "
                f"ats={ats_score:.2f}<{th['strong_min_ats']:.2f})"
            )
    if blended < th["pass_below_blended"] and verdict != "pass":
        return "pass", (
            f"downgraded {verdict} -> pass "
            f"(blended={blended:.2f}<{th['pass_below_blended']:.2f})"
        )
    return verdict, ""


def _candidate_years_claim(profile: Dict[str, Any]) -> int:
    """Resolve the candidate's claimed years of relevant experience.

    Precedence: profile.constraints.claim_years_technical_experience →
    env CANDIDATE_CLAIM_YEARS → derived from sum of experience tenures.
    """
    con = profile.get("constraints") if isinstance(profile.get("constraints"), dict) else {}
    claimed = int(con.get("claim_years_technical_experience") or 0)
    if claimed > 0:
        return claimed
    env_v = os.getenv("CANDIDATE_CLAIM_YEARS")
    if env_v:
        try:
            v = int(env_v)
            if v > 0:
                return v
        except ValueError:
            pass
    # Derive from experience tenure (sum of months across all entries / 12).
    exps = profile.get("experience") if isinstance(profile.get("experience"), list) else []
    months_total = 0
    for exp in exps:
        if not isinstance(exp, dict):
            continue
        sd = str(exp.get("start_date") or "").strip()
        ed = str(exp.get("end_date") or "").strip()
        sm = re.match(r"^(\d{4})-(\d{1,2})", sd)
        em = re.match(r"^(\d{4})-(\d{1,2})", ed)
        if not (sm and em):
            continue
        try:
            sy, smo = int(sm.group(1)), int(sm.group(2))
            ey, emo = int(em.group(1)), int(em.group(2))
            months = (ey - sy) * 12 + (emo - smo)
            if months > 0:
                months_total += months
        except ValueError:
            continue
    if months_total > 0:
        return max(1, months_total // 12)
    return 0


def _yoe_penalty_settings() -> Dict[str, float]:
    """Knobs for the YOE proportional penalty. Tunable via env."""
    return {
        "tolerance_years": float(os.getenv("YOE_GAP_TOLERANCE_YEARS") or 0),
        "penalty_per_year": float(os.getenv("YOE_PENALTY_PER_YEAR") or 0.12),
        "max_penalty": float(os.getenv("YOE_MAX_PENALTY") or 0.85),
    }


def _apply_yoe_cap(
    llm_fit: float, desc: str, profile: Dict[str, Any]
) -> Tuple[float, str, Dict[str, Any]]:
    """Penalize llm_fit when JD demands more years than the candidate claims.

    Returns (new_fit, legacy_note, structured_card_block). The structured
    block goes onto the summary card so the dashboard can surface why the
    score dropped.
    """
    min_y, span = extract_min_years_experience(desc)
    claim = _candidate_years_claim(profile)
    block: Dict[str, Any] = {
        "jd_min_years": min_y,
        "candidate_claim_years": claim,
        "gap_years": 0,
        "penalty_pct": 0,
        "match_span": span,
    }
    if min_y is None or claim <= 0:
        return llm_fit, "", block
    knobs = _yoe_penalty_settings()
    gap = float(min_y) - float(claim) - knobs["tolerance_years"]
    if gap <= 0:
        return llm_fit, "", block
    penalty = min(knobs["max_penalty"], knobs["penalty_per_year"] * gap)
    new_fit = round(max(0.0, llm_fit * (1.0 - penalty)), 4)
    block["gap_years"] = round(gap, 2)
    block["penalty_pct"] = round(penalty * 100, 1)
    note = (
        f"yoe_gap_{int(gap)}y (jd_min={min_y} claim={claim}) "
        f"-> -{int(penalty*100)}%:{span[:40]}"
    )
    return new_fit, note, block


def _seniority_multiplier(profile: Dict[str, Any], seniority_fit: str, title: str) -> Tuple[float, str]:
    sf = (seniority_fit or "").strip().lower()
    mult = 1.0
    bits: List[str] = []
    if sf == "stretch":
        mult *= 0.9
        bits.append("model_stretch")
    elif sf == "overqualified":
        bits.append("model_overqualified")
    else:
        bits.append("aligned_or_unknown")

    tl = (title or "").lower()
    if re.search(r"\b(senior|sr\.|principal|staff|distinguished)\b", tl) and not re.search(
        r"\b(junior|jr\.|associate|entry|graduate|intern)\b", tl
    ):
        band = str((profile.get("identity") or {}).get("experience_band") or "").lower()
        if "junior" in band or "entry" in band:
            mult *= 0.75
            bits.append("title_senior_vs_junior_profile")
    if re.search(r"\b(junior|jr\.|associate|entry)\b", tl) and sf != "stretch":
        mult = min(1.05, mult * 1.04)
        bits.append("title_entry_boost")

    return round(max(0.35, min(1.15, mult)), 4), ",".join(bits)


def _should_auto_filter(
    combined: float,
    verdict: str,
    junk: bool,
    th: Dict[str, Any],
) -> bool:
    if junk:
        return True
    if os.getenv("JOB_PIPELINE_AUTO_FILTER_LOW_FIT", "true").lower() not in ("1", "true", "yes"):
        return False
    if verdict == "pass" and combined < th["auto_close_pass_verdict_combined_below"]:
        return True
    if combined < th["auto_close_combined_below"]:
        return True
    return False


def summarize_pipeline_item(item_id: int, *, force: bool = False) -> Tuple[bool, str]:
    """
    Run LLM triage + full deterministic scoring chain and persist summary_json.

    Normal path: row must be ``ingested``. With ``force=True``, row must be
    ``pending_review`` or ``ranked`` (re-summarize without status hacks).
    """
    row = get_item(item_id)
    if not row:
        return False, "item not found"
    st = str(row.get("status") or "")
    if force:
        if st not in ("pending_review", "ranked"):
            return (
                False,
                f"force=True requires status pending_review or ranked, got {row.get('status')}",
            )
    elif st != "ingested":
        return False, f"item status is {row.get('status')}, expected ingested"

    cfg = load_pipeline_config()
    th = matching_thresholds(cfg)

    key = (os.getenv("OPENAI_API_KEY") or os.getenv("CHATGPT_API_KEY") or "").strip()
    if not key:
        return False, "OPENAI_API_KEY not set"

    desc = row.get("description_text") or ""
    title = row.get("title") or ""
    company = row.get("company_name") or ""
    location = row.get("location") or ""
    salary = row.get("salary_text") or ""

    # Early-exit BEFORE the LLM call: if the title matches the avoid regex
    # (Sales / Senior / Director / Tier 2+ / etc.), close the item without
    # spending tokens. The search-preferences scorer is already deterministic
    # for title-based rejects — running it here lets us skip the entire LLM
    # round-trip on guaranteed-reject postings.
    try:
        from job_pipeline.search_preferences import load_search_preferences

        _prefs = load_search_preferences()
        _avoid_re = _prefs.get("avoid_title_re")
        if _avoid_re is not None and title and _avoid_re.search(title.lower()):
            stub = {
                "prompt_framing_version": PROMPT_FRAMING_VERSION,
                "company": company,
                "role": title,
                "salary": salary or "not listed",
                "location": location,
                "verdict": "pass",
                "verdict_llm": "pass",
                "verdict_downgrade_reason": "",
                "why_match": "",
                "gaps": "",
                "key_requirements": [],
                "application_friction": "",
                "seniority_fit": "",
                "likely_junk": False,
                "junk_reason": "",
                "fit_score_model": 0.0,
                "fit_score_blended": 0.0,
                "fit_score_blended_base": 0.0,
                "fit_score_final": 0.0,
                "filter_reason": "title_avoided_pre_llm",
                "auto_filtered": True,
                "auto_close_reason": "title_avoided",
                "search_preferences": {
                    "auto_close_reason": "title_avoided",
                    "pref_multiplier": 0.0,
                    "preference_notes": [
                        "Early-exit before LLM: title matched the hard-reject avoid regex.",
                    ],
                    "reject": True,
                },
            }
            set_item_summary(
                item_id,
                0.0,
                999,
                "junk",
                stub,
                recommended_resume_id="",
                cover_letter_template_id="",
                status="closed",
            )
            return True, "early_exit_title_avoided"
    except Exception:
        # Never block summarize on the early-exit path — fall through to normal flow.
        pass

    # Second early-exit BEFORE the LLM call: enforce "30 miles from 19107 OR
    # remote-US, period." If the posting is onsite/hybrid outside Philly metro
    # OR unknown-work-mode with no remote signal, close it without spending
    # LLM tokens. Mirrors the deterministic location_policy that runs AFTER
    # the LLM — moving it earlier saves money on guaranteed-reject items
    # (Dublin/London/SF/NYC onsite, etc.).
    try:
        from job_pipeline.location_policy import evaluate_location_policy

        loc_action, _loc_mult, loc_cls, loc_reason = evaluate_location_policy(
            title, location, desc, cfg
        )
        if loc_action == "reject":
            stub = {
                "prompt_framing_version": PROMPT_FRAMING_VERSION,
                "company": company,
                "role": title,
                "salary": salary or "not listed",
                "location": location,
                "verdict": "pass",
                "verdict_llm": "pass",
                "verdict_downgrade_reason": "",
                "why_match": "",
                "gaps": "",
                "key_requirements": [],
                "application_friction": "",
                "seniority_fit": "",
                "likely_junk": False,
                "junk_reason": "",
                "fit_score_model": 0.0,
                "fit_score_blended": 0.0,
                "fit_score_blended_base": 0.0,
                "fit_score_final": 0.0,
                "filter_reason": "location_rejected_pre_llm",
                "auto_filtered": True,
                "auto_close_reason": loc_reason or "outside_metro",
                "location_policy": {
                    "action": "reject",
                    "multiplier": 0.0,
                    "classification": loc_cls,
                    "reason_code": loc_reason,
                    "reject": True,
                },
            }
            set_item_summary(
                item_id,
                0.0,
                999,
                "junk",
                stub,
                recommended_resume_id="",
                cover_letter_template_id="",
                status="closed",
            )
            return True, "early_exit_location_rejected"
    except Exception:
        pass

    resumes: List[Dict[str, Any]] = []
    templates: List[Dict[str, Any]] = []
    skills: List[str] = []
    asset_blob = ""
    strat_block = ""
    # Lane-aware identity calibration: operations-category jobs are scored against the
    # applicant's operations record (the IT-primary identity deliberately discounts ops
    # and was burying genuine coordinator/specialist fits). IT and all other lanes keep
    # the default IT-primary identity unchanged.
    try:
        from job_pipeline.lane_category import classify_category, CAT_OPERATIONS
        from job_pipeline.domain_fit import operations_identity_prompt_block

        if classify_category(title, desc, location) == CAT_OPERATIONS:
            identity_block = operations_identity_prompt_block()
        else:
            identity_block = career_identity_prompt_block()
    except Exception:
        identity_block = career_identity_prompt_block()

    h_fit, h_hits = _heuristic_fit(desc, skills)
    h_junk, h_junk_reason = _heuristic_junk(title, desc, company)

    client = OpenAI(api_key=key)
    model = (
        os.getenv("OPENAI_JOB_SUMMARY_MODEL")
        or os.getenv("CHATGPT_JOB_SUMMARY_MODEL")
        or "gpt-4.1-mini"
    ).strip()
    prompt = (
        "You are a job-search triage assistant. Output ONE JSON object ONLY "
        "(no markdown, no prose). Keys (all required):\n"
        "verdict (string, exactly one of: strong_match | maybe | pass) — "
        "use 'pass' for real jobs that are simply a poor fit for the candidate,\n"
        "fit_score_0_1 (number 0-1, calibrated: 0.85+ only for excellent matches),\n"
        "key_requirements (array of 3-7 short strings — the must-have skills, "
        "certs, years, or tools called out by the JD, e.g. "
        '["3+ years help desk", "Active Directory", "M365 admin", "CompTIA A+"]),\n'
        "application_friction (string: 'low' | 'medium' | 'high'),\n"
        "seniority_fit (string: overqualified | aligned | stretch),\n"
        "likely_junk (boolean): set true ONLY for postings that are NOT genuine "
        "job openings — talent-pool / 'general interest' / 'future opportunity' "
        "submissions, MLM / commission-only / 1099 piecework, duplicate or "
        "vague brochure-only postings, non-jobs. A real opening at a real "
        "company is NEVER junk, even if it's a poor fit. Use verdict='pass' "
        "for poor-fit signal, NOT likely_junk.\n"
        "recommended_resume_id (string, must match a given resume id),\n"
        "recommended_cover_template_id (string, must match a given cover letter template id).\n\n"
        f"POSTING_TITLE: {title}\nCOMPANY: {company}\nLOCATION: {location}\nSALARY: {salary}\n\n"
        f"DESCRIPTION:\n{desc[:4000]}\n\n{asset_blob}\n\n{strat_block}\n\n{identity_block}\n"
    )
    max_retries = max(
        1,
        int(
            os.getenv("OPENAI_SUMMARY_MAX_RETRIES")
            or os.getenv("GEMINI_SUMMARY_MAX_RETRIES")
            or "6"
        ),
    )
    base_sleep = float(
        os.getenv("OPENAI_SUMMARY_RETRY_BASE_SEC")
        or os.getenv("GEMINI_SUMMARY_RETRY_BASE_SEC")
        or "6"
    )
    summary_temp = resolve_openai_temperature(
        model,
        explicit=0.2,
        env_names=("OPENAI_SUMMARY_TEMPERATURE", "OPENAI_WRITING_TEMPERATURE", "OPENAI_TEMPERATURE"),
    )
    text = ""
    last_err: str = ""
    for attempt in range(max_retries):
        try:
            create_kwargs: Dict[str, Any] = {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a strict job-search triage assistant. "
                            "Return exactly one valid JSON object and no markdown."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            }
            if summary_temp is not None:
                create_kwargs["temperature"] = summary_temp
            resp = client.chat.completions.create(**create_kwargs)
            text = (resp.choices[0].message.content or "").strip()
            break
        except Exception as e:
            last_err = str(e)
            if is_temperature_parameter_error(e):
                summary_temp = None
                continue
            es = last_err.lower()
            retryable = (
                "503" in last_err
                or "429" in last_err
                or "500" in last_err
                or "502" in last_err
                or "504" in last_err
                or "unavailable" in es
                or "rate limit" in es
                or "temporar" in es
                or "resource_exhausted" in es
                or "overloaded" in es
            )
            if retryable and attempt < max_retries - 1:
                delay = min(120.0, base_sleep * (1.6**attempt))
                time.sleep(delay)
                continue
            return False, f"openai_request_failed: {last_err}"
    if not text:
        return False, f"openai_request_failed: {last_err or 'empty response'}"
    try:
        obj = _parse_json_from_model(text)
    except Exception as e:
        return False, f"model JSON parse failed: {e}"

    llm_fit = float(obj.get("fit_score_0_1", 0.5))
    llm_fit = max(0.0, min(1.0, llm_fit))
    profile = load_career_profile()
    llm_fit, yoe_cap_note, yoe_block = _apply_yoe_cap(llm_fit, desc, profile)

    res_blob, resume_text_src = build_canonical_resume_text()
    ats_out = compute_ats_overlap(
        desc,
        canonical_resume_blob=res_blob,
        resume_skill_terms=skills,
    )
    ats_fit = float(ats_out.get("ats_score") or 0.0)
    ats_ready = (
        not ats_out.get("ats_skipped")
        and ats_out.get("ats_score") is not None
        and len((res_blob or "").strip()) > 40
    )
    if ats_ready:
        base_combined = round(0.52 * llm_fit + 0.33 * h_fit + 0.15 * ats_fit, 3)
        blend_formula = "0.52*model + 0.33*heuristic + 0.15*ats_overlap"
    else:
        base_combined = round(0.58 * llm_fit + 0.42 * h_fit, 3)
        blend_formula = "0.58*model + 0.42*heuristic (ats skipped: thin resume text)"

    domain_res = calculate_domain_fit(title, desc, profile)
    combined_after_domain = merge_blended_with_domain(base_combined, domain_res)

    seniority_fit_raw = str(obj.get("seniority_fit") or "").strip()
    sen_mult, sen_bits = _seniority_multiplier(profile, seniority_fit_raw, title)
    combined_after_seniority = round(min(1.0, max(0.0, combined_after_domain * sen_mult)), 3)

    loc_action, loc_mult, loc_cls, loc_code = evaluate_location_policy(title, location, desc, cfg)
    loc_reject = loc_action == "reject"
    if loc_reject:
        combined_after_location = combined_after_seniority
    else:
        combined_after_location = round(min(1.0, max(0.0, combined_after_seniority * loc_mult)), 3)

    # --- Truth-limit fit pass --------------------------------------------
    # Penalize jobs whose REQUIRED skills are on the candidate's evidence.json
    # no-claim list (AD admin, M365 admin, etc.). The summarizer LLM doesn't
    # see truth_limits and routinely scores these "strong" anyway; this is the
    # deterministic gate. Multiplier and blocked-skill list are surfaced on
    # the card for transparency.
    profile_text = load_consolidated_profile_text() or ""
    tlfit = _evaluate_truth_limit_fit(desc, profile_text)
    tl_mult = float(tlfit.get("multiplier") or 1.0)
    if loc_reject:
        combined_after_truthlimits = combined_after_location
    else:
        combined_after_truthlimits = round(min(1.0, max(0.0, combined_after_location * tl_mult)), 4)

    # --- Hand-edited search-preferences pass (search_preferences.md) ------
    combined_after_preferences, prefs_card, pref_reject, pref_close_reason, fit_raw = (
        apply_search_preferences_stage(
            cfg,
            title=title,
            description_text=desc,
            location=location,
            salary_text=salary,
            source=str(row.get("source") or ""),
            combined_after_location=combined_after_truthlimits,
            loc_reject=loc_reject,
        )
    )
    pref_mult = float(prefs_card.get("pref_multiplier") or 1.0)

    combined = combined_after_preferences

    verdict = str(obj.get("verdict") or "maybe").strip().lower()
    if verdict not in ("strong_match", "maybe", "pass"):
        verdict = "maybe"

    # Verdict-score reconciliation: the LLM overrates fit ~25% of the time.
    # If the blended fit or ATS overlap doesn't clear the strong_match floor,
    # downgrade. Logged on the card for transparency.
    _ats_for_floor = float((ats_out or {}).get("ats_score") or 0.0)
    _llm_verdict = verdict
    verdict, _verdict_change_reason = _reconcile_verdict_with_scores(
        verdict, float(combined or 0.0), _ats_for_floor
    )

    model_junk = bool(obj.get("likely_junk"))
    likely_junk = h_junk or model_junk
    junk_reason = (h_junk_reason or str(obj.get("junk_reason") or "")).strip()

    rid = str(obj.get("recommended_resume_id") or "").strip()
    valid_resume_ids = {
        str(r.get("id") or "").strip()
        for r in resumes
        if isinstance(r, dict) and str(r.get("id") or "").strip()
    }
    if rid not in valid_resume_ids and resumes:
        rid = str(resumes[0].get("id") or "")
    elif not rid and resumes:
        rid = str(resumes[0].get("id") or "")

    tid = ""
    if templates:
        tid = str(templates[0].get("id") or "")

    strat_meta: Dict[str, Any] = {}

    list_rank = _compute_list_rank(fit_raw, verdict, likely_junk)
    qbucket = _quality_bucket(verdict, likely_junk)
    auto_close = _should_auto_filter(combined, verdict, likely_junk, th)
    sal_close, sal_reason = salary_hard_gate(row, cfg)
    hard_close = auto_close or sal_close or loc_reject or pref_reject
    target_status = "closed" if hard_close else "pending_review"

    close_category = ""
    close_detail = ""
    if pref_reject:
        close_category = "search_preferences"
        close_detail = pref_close_reason or "reject"
        filter_reason = f"search_preferences:{pref_close_reason or 'reject'}"
    elif loc_reject:
        close_category = "location"
        close_detail = loc_code or "rejected"
        filter_reason = f"location_policy:{loc_code or 'rejected'}"
    elif sal_close:
        close_category = "salary"
        close_detail = sal_reason or "salary_gate"
        filter_reason = sal_reason
    elif auto_close:
        if likely_junk:
            close_category = "junk"
            close_detail = "junk_or_noise"
            filter_reason = "junk_or_noise"
        elif verdict == "pass" and combined < th["auto_close_pass_verdict_combined_below"]:
            close_category = "threshold"
            close_detail = "pass_verdict_low_combined"
            filter_reason = "low_fit_or_pass"
        elif combined < th["auto_close_combined_below"]:
            close_category = "threshold"
            close_detail = "low_combined_score"
            filter_reason = "low_combined_score"
        else:
            close_category = "threshold"
            close_detail = "auto_closed"
            filter_reason = "auto_closed"
    else:
        filter_reason = ""

    card: Dict[str, Any] = {
        "fit_score_raw": fit_raw,
        "prompt_framing_version": PROMPT_FRAMING_VERSION,
        "company": obj.get("company") or company,
        "role": obj.get("role") or title,
        "salary": obj.get("salary") or salary or "not listed",
        "location": obj.get("location") or location,
        "headline_one_line": (obj.get("headline_one_line") or "")[:120],
        "verdict": verdict,
        "verdict_llm": _llm_verdict,
        "verdict_downgrade_reason": _verdict_change_reason,
        "why_match": obj.get("why_match") or "",
        "gaps": obj.get("gaps") or "",
        "key_requirements": [
            str(x).strip()[:80]
            for x in (obj.get("key_requirements") or [])
            if str(x).strip()
        ][:10],
        "application_friction": obj.get("application_friction") or "",
        "seniority_fit": seniority_fit_raw or (obj.get("seniority_fit") or ""),
        "seniority_multiplier": sen_mult,
        "seniority_notes": sen_bits,
        "time_to_apply_minutes_estimate": obj.get("time_to_apply_minutes_estimate"),
        "custom_cover_worth_it": bool(obj.get("custom_cover_worth_it", True)),
        "fit_score_model": llm_fit,
        "deterministic_yoe_cap": yoe_cap_note,
        "fit_score_heuristic": round(h_fit, 3),
        "ats_overlap": ats_out,
        "ats_resume_source": resume_text_src,
        "fit_score_blended_base": base_combined,
        "fit_score_mid_domain": combined_after_domain,
        "fit_score_after_domain_then_seniority": combined_after_seniority,
        "fit_score_after_location": combined_after_location,
        "fit_score_blended": combined,
        "search_preferences": dict(prefs_card),
        "location_policy": {
            "action": loc_action,
            "multiplier": loc_mult,
            "classification": loc_cls,
            "reason_code": loc_code,
            "reject": loc_reject,
        },
        "domain_fit": {
            "domain_score": domain_res.get("domain_score"),
            "domain_multiplier": domain_res.get("domain_multiplier"),
            "matched_families": domain_res.get("matched_families"),
            "penalized_families": domain_res.get("penalized_families"),
            "detected_families": domain_res.get("detected_families"),
            "queue_reason": domain_res.get("queue_reason"),
            "reasons": domain_res.get("reasons"),
            "title_avoid_hit": domain_res.get("title_avoid_hit"),
        },
        "truth_limit_fit": {
            "multiplier": tl_mult,
            "blocked_required_skills": tlfit.get("blocked") or [],
            "not_true_required_skills": tlfit.get("not_true") or [],
            "direct_supported_skills": tlfit.get("direct") or [],
        },
        "yoe_fit": yoe_block,
        "likely_junk": likely_junk,
        "junk_reason": junk_reason,
        "recommended_resume_id": rid,
        "auto_filtered": target_status == "closed",
        "filter_reason": filter_reason,
        "close_reason_category": close_category if hard_close else "",
        "close_reason_detail": close_detail if hard_close else "",
        "asset_strategy": strat_meta.get("strategy"),
        "strategy_overrode_resume": bool(strat_meta.get("overrode_resume")),
        "strategy_overrode_template": bool(strat_meta.get("overrode_template")),
        "strategy_override_notes": {
            "resume": strat_meta.get("override_resume_reason"),
            "template": strat_meta.get("override_template_reason"),
        },
    }

    if th["explain_scores"]:
        auto_on = os.getenv("JOB_PIPELINE_AUTO_FILTER_LOW_FIT", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        card["score_explanation"] = {
            "blended_formula": blend_formula,
            "domain_merge": (
                "combined = blended_base * domain_multiplier; "
                "then * seniority_mult; then * location_mult unless reject; "
                "then * search_preferences_pref_multiplier unless reject"
            ),
            "fit_score_blended_base": base_combined,
            "fit_score_after_domain_only": combined_after_domain,
            "fit_score_after_domain_then_seniority": combined_after_seniority,
            "fit_score_after_location": combined_after_location,
            "search_preferences_multiplier": pref_mult,
            "search_preferences_effective_multiplier": prefs_card.get("effective_multiplier_applied"),
            "search_preferences_reject_reason": prefs_card.get("auto_close_reason"),
            "fit_score_final": combined,
            "fit_score_raw": fit_raw,
            "domain_score": domain_res.get("domain_score"),
            "domain_multiplier": domain_res.get("domain_multiplier"),
            "model_fit": llm_fit,
            "heuristic_fit": round(h_fit, 3),
            "heuristic_skill_hits": h_hits,
            "ats_overlap_score": ats_out.get("ats_score"),
            "verdict": verdict,
            "list_rank": list_rank,
            "threshold_close_combined_below": th["auto_close_combined_below"],
            "threshold_pass_verdict_combined_below": th["auto_close_pass_verdict_combined_below"],
            "auto_low_fit_filter_enabled": auto_on,
            "salary_hard_gate": bool(th["salary_hard_gate"]),
            "min_salary_usd_config": th["min_salary_usd"],
        }

    # Truth-grounding gate: strip any forbidden-claim phrasing from card text
    # fields (why_match, headline) BEFORE persisting. The summarizer LLM does
    # not see evidence.json truth_limits, so the deterministic scrub here is
    # what keeps "Active Directory basics" etc. out of the dashboard rationale.
    try:
        from job_pipeline.integrity_guards import scrub_card_no_claim_terms

        scrub_card_no_claim_terms(card)
    except Exception:
        pass

    # Learning-gaps cache: append JD requirements Carlos doesn't yet have
    # grounded in career_master.md / consolidated_profile.json. Best-effort —
    # never block summarize on this.
    try:
        from job_pipeline.learning_gaps import update_learning_gaps

        update_learning_gaps(
            item_id,
            card.get("key_requirements") or [],
            job_title=str(card.get("role") or title or "")[:120],
            company=str(card.get("company") or company or "")[:80],
        )
    except Exception:
        pass

    ok = set_item_summary(
        item_id,
        combined,
        card,
        rid,
        tid,
        list_rank=list_rank,
        quality_bucket=qbucket,
        target_status=target_status,
        force=force,
    )
    if not ok:
        return False, "failed to update row (wrong status?)"
    # Assign the lane category (IT Help Desk / IT General / Operations / Remote)
    # so the dashboard tabs and per-agent queries have a single source of truth.
    # Best-effort — never fail a summarize over categorization.
    try:
        from job_pipeline.db import set_item_category

        set_item_category(item_id)
    except Exception:
        pass
    return True, "summarized"


def run_summarize_batch(
    limit: int = 15,
    *,
    on_progress: Optional[SummarizeProgressFn] = None,
) -> Dict[str, Any]:
    ids = list_items_by_statuses(["ingested"], limit=max(1, limit))
    ok_l = []
    err_l = []
    filtered_n = 0
    review_n = 0
    close_reasons: Dict[str, int] = {}
    total = len(ids)
    for n, iid in enumerate(ids, start=1):
        ok, msg = summarize_pipeline_item(iid)
        if on_progress is not None:
            try:
                on_progress(n, total, int(iid), bool(ok), str(msg or ""))
            except Exception:
                pass
        if ok:
            ok_l.append(iid)
            row = get_item(iid)
            if row and row.get("status") == "closed":
                filtered_n += 1
                sj = parse_summary_json(row.get("summary_json"))
                cat = str(sj.get("close_reason_category") or "unknown").strip() or "unknown"
                close_reasons[cat] = close_reasons.get(cat, 0) + 1
            else:
                review_n += 1
        else:
            err_l.append({"item_id": iid, "error": msg})
    return {
        "ok": True,
        "summarized": ok_l,
        "pending_review_count": review_n,
        "auto_filtered_count": filtered_n,
        "close_reason_counts": close_reasons,
        "errors": err_l,
    }


def run_summarize_all(
    *,
    batch_size: int = 50,
    max_batches: int = 100,
    max_minutes: float = 45.0,
    should_stop: Optional[Any] = None,
    on_progress: Optional[SummarizeProgressFn] = None,
) -> Dict[str, Any]:
    """Drain every ``ingested`` row via repeated summarize batches."""
    import time as _time

    batch_size = max(1, int(batch_size))
    max_batches = max(1, int(max_batches))
    deadline = _time.monotonic() + max(1.0, float(max_minutes)) * 60.0
    totals = {
        "summarized": 0,
        "pending_review_count": 0,
        "auto_filtered_count": 0,
        "errors": [],
        "batches": 0,
        "close_reason_counts": {},
        "stopped_early": False,
        "stop_reason": "",
    }
    while totals["batches"] < max_batches and _time.monotonic() < deadline:
        if should_stop and should_stop():
            totals["stopped_early"] = True
            totals["stop_reason"] = "cancelled"
            break
        remaining = count_items_by_status("ingested")
        if remaining <= 0:
            break
        batch = run_summarize_batch(
            limit=min(batch_size, remaining),
            on_progress=on_progress,
        )
        totals["batches"] += 1
        totals["summarized"] += len(batch.get("summarized") or [])
        totals["pending_review_count"] += int(batch.get("pending_review_count") or 0)
        totals["auto_filtered_count"] += int(batch.get("auto_filtered_count") or 0)
        totals["errors"].extend(batch.get("errors") or [])
        for cat, n in (batch.get("close_reason_counts") or {}).items():
            totals["close_reason_counts"][cat] = totals["close_reason_counts"].get(cat, 0) + int(n)
        if batch.get("errors"):
            totals["stopped_early"] = True
            totals["stop_reason"] = "batch_errors"
            break
    else:
        if count_items_by_status("ingested") > 0:
            totals["stopped_early"] = True
            totals["stop_reason"] = "max_batches_or_time"
    totals["ingested_remaining"] = count_items_by_status("ingested")
    totals["ok"] = not totals["errors"]
    return totals
