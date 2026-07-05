import json
import sys
import html
import re
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    # override=True: defeat OS-env shadowing (Windows shells occasionally
    # inject empty API_KEY vars at the OS level that silently block .env).
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass

import streamlit as st

from job_pipeline.lane_category import CATEGORY_ORDER, category_label

logger = logging.getLogger(__name__)


@st.cache_data(show_spinner=False)
def _cached_file_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


# ---------------------------------------------------------------------------
# Streamlit rerun cache.
#
# Streamlit re-runs the WHOLE script on every widget interaction. With 30
# queue rows × ~10 widgets per row, even fast DB calls (200ms total) get
# repeated every click — and the WebSocket re-sync amplifies that into
# multi-second pauses (the CDP-screenshot freezes we observed).
#
# We cache the SHAPE-stable stats calls (counts, breakdowns) keyed by a
# session-state invalidator. User actions that mutate state (approve, skip,
# build, decide, outcome) bump the invalidator so cached data is refreshed
# exactly when it needs to be — never on idle clicks.
# ---------------------------------------------------------------------------

def _cache_token() -> int:
    """Read-only cache key. Bumped by `_invalidate_caches()` on mutations."""
    return int(st.session_state.get("_cache_token", 0))


def _invalidate_caches() -> None:
    st.session_state["_cache_token"] = _cache_token() + 1


def _rerun() -> None:
    """Bump the cache token and trigger a Streamlit rerun. Use this anywhere
    you would call Streamlit's rerun after a user action that mutates state —
    it guarantees the cached stats/counts will refresh on the next script run."""
    _invalidate_caches()
    st.rerun()


@st.cache_data(show_spinner=False, ttl=30)
def _cached_pipeline_stats(_tok: int) -> Dict[str, Any]:
    svc, _ = _load_services()[:2]
    return svc.svc_pipeline_stats() if svc else {}


@st.cache_data(show_spinner=False, ttl=30)
def _cached_closed_breakdown(_tok: int) -> Dict[str, Any]:
    svc, _ = _load_services()[:2]
    return svc.svc_closed_reason_breakdown() if svc else {}


@st.cache_data(show_spinner=False, ttl=20)
def _cached_count_pending_review(_tok: int, min_rank: float, source: str = "") -> int:
    svc, _ = _load_services()[:2]
    if not svc:
        return 0
    return int(svc.svc_count_pending_review(min_rank, source=source) or 0)


@st.cache_data(show_spinner=False, ttl=20)
def _cached_category_counts(_tok: int, status: str, min_rank: float, source: str = "") -> Dict[str, Any]:
    svc, _ = _load_services()[:2]
    if not svc:
        return {}
    return svc.svc_category_counts(status, min_list_rank=min_rank, source=source or None) or {}


@st.cache_data(show_spinner=False, ttl=20)
def _cached_count_completed(_tok: int) -> int:
    svc, _ = _load_services()[:2]
    if not svc:
        return 0
    return int(svc.svc_count_completed() or 0)


@st.cache_data(show_spinner=False, ttl=20)
def _cached_source_counts(_tok: int, status: str, min_rank: float) -> Dict[str, Any]:
    svc, _ = _load_services()[:2]
    if not svc:
        return {}
    if not status:
        return svc.svc_source_counts("", min_list_rank=min_rank) or {}
    return svc.svc_source_counts(status, min_list_rank=min_rank) or {}


def _record_recent_build(item_id: int) -> None:
    """Track recently built queue items so Package Ready can pin them to the top."""
    iid = int(item_id)
    recent = [int(x) for x in (st.session_state.get("recent_built_ids") or [])]
    recent = [iid] + [x for x in recent if x != iid]
    st.session_state["recent_built_ids"] = recent[:30]


def _render_clear_all_jobs_panel(svc, *, key_prefix: str = "clear") -> None:
    preserved = int(svc.svc_count_completed() or 0)
    st.warning(
        "Permanently deletes **non-completed** jobs (queue, package ready, closed, etc.). "
        f"**{preserved} completed** application(s) (submitted / responded / rejected) are **kept**. "
        "Gap answers are kept; PDF files on disk are not deleted."
    )
    result = st.session_state.get("clear_jobs_result")
    if result:
        if result.get("ok"):
            st.success(
                f"Removed {result.get('items_deleted', 0)} pipeline items and "
                f"{result.get('postings_deleted', 0)} postings. "
                f"Preserved {result.get('items_preserved', 0)} completed application(s)."
            )
        else:
            st.error(result.get("error") or "Clear failed.")

    armed_at = st.session_state.get("clear_jobs_armed_at")
    if armed_at is None:
        ack = st.checkbox(
            "I understand this deletes non-completed jobs and cannot be undone",
            key=f"{key_prefix}_jobs_ack",
        )
        if st.button(
            "Start 10-second countdown",
            disabled=not ack,
            key=f"{key_prefix}_jobs_start",
            use_container_width=True,
        ):
            st.session_state["clear_jobs_armed_at"] = time.time()
            st.session_state.pop("clear_jobs_result", None)
            st.session_state.pop(f"{key_prefix}_delete_confirm_text", None)
            _rerun()
        return

    @st.fragment(run_every=timedelta(seconds=1))
    def _clear_jobs_countdown() -> None:
        started = float(st.session_state.get("clear_jobs_armed_at") or 0)
        remaining = max(0, 10 - int(time.time() - started))
        if remaining > 0:
            st.error(
                f"Are you sure? Confirm unlocks in **{remaining}** second"
                f"{'s' if remaining != 1 else ''}."
            )
            st.button(
                "DELETE ALL JOBS NOW",
                disabled=True,
                key=f"{key_prefix}_jobs_confirm_wait",
                use_container_width=True,
            )
        else:
            st.error("Type DELETE below to permanently remove non-completed jobs.")
            typed = st.text_input(
                'Type "DELETE" to confirm',
                key=f"{key_prefix}_delete_confirm_text",
            )
            if st.button(
                "DELETE ALL JOBS NOW",
                type="primary",
                key=f"{key_prefix}_jobs_confirm",
                disabled=(typed.strip().upper() != "DELETE"),
                use_container_width=True,
            ):
                out = svc.svc_clear_all_jobs()
                st.session_state.pop("clear_jobs_armed_at", None)
                st.session_state["clear_jobs_result"] = out
                st.session_state.pop("recent_built_ids", None)
                st.session_state.pop("last_package_result", None)
                st.session_state.pop(f"{key_prefix}_delete_confirm_text", None)
                _rerun()
        if st.button("Cancel", key=f"{key_prefix}_jobs_cancel", use_container_width=True):
            st.session_state.pop("clear_jobs_armed_at", None)
            st.session_state.pop(f"{key_prefix}_delete_confirm_text", None)
            _rerun()

    _clear_jobs_countdown()


def _render_jd_gaps_panel(gaps: List[Dict[str, Any]], *, key_prefix: str = "gap") -> None:
    """Show structured JD requirement gaps (named_req highlighted)."""
    if not gaps:
        st.caption("No JD requirement gaps detected for this posting.")
        return
    named = [g for g in gaps if g.get("category") == "named_req"]
    other = [g for g in gaps if g.get("category") != "named_req"]
    if named:
        st.markdown("**Named JD requirements** (not supported in profile — do not fabricate)")
        for i, g in enumerate(named):
            sev = g.get("severity") or "medium"
            req = g.get("requirement") or "requirement"
            st.markdown(f"- `{sev}` **{req}**")
            if g.get("question"):
                st.caption(str(g.get("question")))
            sug = (g.get("suggested_answer") or "").strip()
            if sug:
                st.caption(f"Suggested: {sug}")
    if other:
        with st.expander(f"Other gaps ({len(other)})", expanded=not named):
            for g in other:
                st.markdown(f"- **{g.get('requirement')}** ({g.get('severity', 'medium')})")

st.set_page_config(
    page_title="Job Application Dashboard",
    page_icon="🎯",
    layout="wide",
)


# NOTE: NOT cached via @st.cache_resource — caching the `svc` module reference
# means edits to job_pipeline/service.py or job_pipeline/summarize.py don't
# take effect until Streamlit is restarted. The import itself is cheap
# (~milliseconds since Python's own import cache handles it). Keeping this
# uncached lets file-watcher reloads pick up changes immediately.
def _load_services():
    try:
        repo_root = _REPO_ROOT
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        import job_pipeline.service as svc
        from application_assets import load_application_assets_dict

        return svc, load_application_assets_dict, None
    except Exception as e:  # pragma: no cover - streamlit runtime path
        return None, None, str(e)


def _get_services():
    svc, assets_fn, err = _load_services()
    if err:
        st.error(f"Pipeline import failed: {err}")
        st.stop()
    return svc, assets_fn


def _card_data(row: Dict[str, Any]) -> Dict[str, Any]:
    sj = row.get("summary_json") or {}
    if isinstance(sj, str):
        try:
            sj = json.loads(sj)
        except Exception:
            sj = {}
    loc_p = sj.get("location_policy") if isinstance(sj.get("location_policy"), dict) else {}
    ats = sj.get("ats_overlap") if isinstance(sj.get("ats_overlap"), dict) else {}
    domain = sj.get("domain_fit") if isinstance(sj.get("domain_fit"), dict) else {}
    prefs = sj.get("search_preferences") if isinstance(sj.get("search_preferences"), dict) else {}
    score_exp = sj.get("score_explanation") if isinstance(sj.get("score_explanation"), dict) else {}
    return {
        "verdict": sj.get("verdict") or "maybe",
        "why": sj.get("why_match") or sj.get("why") or "",
        "gaps": sj.get("gaps") or "",
        "headline": sj.get("headline_one_line") or "",
        "key_requirements": list(sj.get("key_requirements") or []),
        "resume_id": sj.get("recommended_resume_id") or row.get("recommended_resume_id") or "",
        "salary": sj.get("salary") or row.get("salary_text") or "—",
        "time_to_apply": sj.get("time_to_apply_minutes_estimate"),
        "friction": sj.get("application_friction") or "",
        "seniority_fit": sj.get("seniority_fit") or "",
        "seniority_mult": sj.get("seniority_multiplier"),
        "seniority_notes": sj.get("seniority_notes") or "",
        "yoe_cap_note": sj.get("deterministic_yoe_cap") or "",
        "ats": ats,
        "loc": loc_p,
        "domain": domain,
        "prefs": prefs,
        "score_exp": score_exp,
        "junk": bool(sj.get("likely_junk")),
        "junk_reason": sj.get("junk_reason") or "",
        "auto_filtered": bool(sj.get("auto_filtered")),
        "filter_reason": sj.get("filter_reason") or "",
        "fit_model": sj.get("fit_score_model"),
        "fit_heuristic": sj.get("fit_score_heuristic"),
        "fit_base": sj.get("fit_score_blended_base"),
        "fit_after_domain": sj.get("fit_score_mid_domain"),
        "fit_after_seniority": sj.get("fit_score_after_domain_then_seniority"),
        "fit_after_location": sj.get("fit_score_after_location"),
        "fit_score_raw": sj.get("fit_score_raw"),
        "fit_final": sj.get("fit_score_blended"),
    }


def _truncate(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _first_sentence(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", t, maxsplit=1)
    first = parts[0].strip()
    return first or t


def _pretty_salary(text: str) -> str:
    """Parse messy salary_text into a compact display string (e.g. '$60K-$70K/yr').
    Returns "" when no salary signal can be extracted so the caller can skip the badge.
    Handles: '60000 - 70000 USD YEAR', '$60,000 - $70,000', '$60K-$70K', '$25/hr',
    'Up to $80,000', '$70K+', 'DOE', etc.
    """
    s = (text or "").strip()
    if not s or s == "—":
        return ""
    raw = s
    s_low = s.lower().replace(",", "")
    m = re.search(r"\$?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s*per\s*)?\s*(?:hr|hour|hourly)\b", s_low)
    if m:
        try:
            rate = float(m.group(1))
            annual_k = int(round(rate * 2080 / 1000))
            rate_disp = f"${int(rate)}" if rate == int(rate) else f"${rate:g}"
            return f"{rate_disp}/hr (~${annual_k}K/yr)"
        except ValueError:
            pass
    m_k = re.search(r"\$?\s*(\d{1,3})\s*k\s*[-–—]\s*\$?\s*(\d{1,3})\s*k", s_low)
    if m_k:
        try:
            return f"${int(m_k.group(1))}K-${int(m_k.group(2))}K/yr"
        except ValueError:
            pass
    nums = [int(n) for n in re.findall(r"\b(\d{4,6})\b", s_low)]
    if len(nums) >= 2:
        lo, hi = sorted(nums[:2])
        return f"${lo // 1000}K-${hi // 1000}K/yr"
    if len(nums) == 1:
        n = nums[0]
        if n >= 1000:
            if re.search(r"\bup\s+to\b", s_low):
                return f"Up to ${n // 1000}K/yr"
            if re.search(r"\+|\bfrom\b|\bstart(?:ing)?\b", s_low):
                return f"${n // 1000}K+/yr"
            return f"${n // 1000}K/yr"
    m_k1 = re.search(r"\$?\s*(\d{1,3})\s*k\b", s_low)
    if m_k1:
        try:
            return f"${int(m_k1.group(1))}K/yr"
        except ValueError:
            pass
    for kw in ("doe", "competitive", "negotiable", "depending on experience"):
        if kw in s_low:
            return "DOE"
    return raw[:30] if len(raw) <= 30 else ""


def _salary_badge_html(salary_text: str) -> str:
    """Green chip showing parsed salary range. Empty string if nothing parseable."""
    label = _pretty_salary(salary_text)
    if not label:
        return ""
    tooltip = html.escape((salary_text or "").strip())
    return (
        f'<span style="background:#ecfdf5;color:#065f46;padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;white-space:nowrap;" title="{tooltip}">'
        f'💰 {html.escape(label)}</span>'
    )


def _fit_badge_html(
    fit: Optional[float],
    *,
    fit_score_raw: Optional[Any] = None,
) -> str:
    if fit is None:
        return '<span style="background:#e5e7eb;color:#374151;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;">Fit —</span>'
    try:
        fv = float(fit)
    except (TypeError, ValueError):
        fv = 0.0
    pct = min(100, int(round(fv * 100)))
    headroom = ""
    try:
        if fit_score_raw is not None and float(fit_score_raw) > 1.0 + 1e-9:
            headroom = "★"
    except (TypeError, ValueError):
        pass
    if pct >= 55:
        bg, fg = "#dcfce7", "#166534"
    elif pct >= 40:
        bg, fg = "#fef3c7", "#92400e"
    else:
        bg, fg = "#e5e7eb", "#374151"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;">Fit {pct}%{html.escape(headroom)}</span>'
    )


def _verdict_badge_html(verdict: str) -> str:
    v = (verdict or "").strip().lower()
    if v in ("strong_match", "strong"):
        bg, fg, label = "#dcfce7", "#166534", "Strong"
    elif v == "maybe":
        bg, fg, label = "#fef3c7", "#92400e", "Maybe"
    else:
        bg, fg, label = "#e5e7eb", "#374151", "Skip"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;">{html.escape(label)}</span>'
    )


def _loc_badge_html(loc: Dict[str, Any]) -> str:
    cls = (loc.get("classification") or "").strip().lower()
    action = (loc.get("action") or "").strip().lower()
    if not cls or cls == "unknown":
        if action != "reject":
            return ""
    mult = loc.get("multiplier")
    icon, label = {
        "remote": ("🏠", "Remote"),
        "hybrid": ("🏢", "Hybrid"),
        "onsite": ("📍", "Onsite"),
        "unknown": ("", "Unknown"),
    }.get(cls, ("", "Unknown"))
    if not label:
        return ""
    if action == "reject":
        bg, fg = "#fecaca", "#7f1d1d"
        label = label + " ✕"
    elif action == "accept" and cls == "remote":
        bg, fg = "#dcfce7", "#166534"
    elif action == "accept":
        bg, fg = "#dbeafe", "#1e40af"
    elif action == "neutral":
        bg, fg = "#f3f4f6", "#374151"
    else:
        bg, fg = "#e5e7eb", "#374151"
    suffix = ""
    try:
        if mult is not None and abs(float(mult) - 1.0) > 0.01 and action != "reject":
            suffix = f" ×{float(mult):.2f}"
    except (TypeError, ValueError):
        pass
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;">{icon} {label}{suffix}</span>'
    )


def _friction_badge_html(friction: str) -> str:
    f = (friction or "").strip().lower()
    if not f:
        return ""
    if f.startswith("low"):
        bg, fg = "#dcfce7", "#166534"
    elif f.startswith("med"):
        bg, fg = "#fef3c7", "#92400e"
    elif f.startswith("high"):
        bg, fg = "#fee2e2", "#991b1b"
    else:
        bg, fg = "#e5e7eb", "#374151"
    label = friction[:24]
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;" title="{html.escape(friction)}">⏱ {html.escape(label)}</span>'
    )


def _ats_badge_html(ats: Dict[str, Any]) -> str:
    if not isinstance(ats, dict):
        return ""
    score = ats.get("ats_score")
    if score is None:
        return ""
    try:
        pct = int(round(float(score) * 100))
    except (TypeError, ValueError):
        return ""
    if pct >= 35:
        bg, fg = "#dcfce7", "#166534"
    elif pct >= 18:
        bg, fg = "#fef3c7", "#92400e"
    else:
        bg, fg = "#e5e7eb", "#374151"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;">ATS {pct}%</span>'
    )


def _seniority_badge_html(sen_fit: str, mult: Any) -> str:
    if mult is None:
        return ""
    try:
        m = float(mult)
    except (TypeError, ValueError):
        return ""
    if 0.85 <= m <= 1.03:
        return ""  # neutral, omit to reduce noise
    if m < 0.85:
        bg, fg, icon = "#fee2e2", "#991b1b", "⬇"
        default_label = "stretch"
    else:
        bg, fg, icon = "#dcfce7", "#166534", "⬆"
        default_label = "boost"
    label = (sen_fit or default_label).strip().lower() or default_label
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:999px;'
        f'font-size:12px;font-weight:600;">{icon} {html.escape(label)} ×{m:.2f}</span>'
    )


def _usd_compact(n: Any) -> str:
    """Format integers like 62000 -> `$62k` for compact card rows."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "—"
    if v >= 1000:
        return f"${v // 1000}k"
    return f"${v}"


def _render_prefs_streamlit(prefs: Dict[str, Any]) -> None:
    """Draw ``search_preferences`` diagnostics alongside domain / location panels."""
    if not prefs:
        st.caption("No search_preferences block stored on this summary.")
        return

    ac = prefs.get("auto_close_reason")
    if ac:
        st.error(_format_filter_reason(f"search_preferences:{ac}"))

    c1, c2, c3 = st.columns(3)
    pm = prefs.get("pref_multiplier")
    with c1:
        if pm is not None:
            try:
                st.markdown(f"**Pref multiplier:** `{float(pm):.2f}x`")
            except (TypeError, ValueError):
                st.markdown("**Pref multiplier:** —")
        else:
            st.markdown("**Pref multiplier:** —")
    wm = str(prefs.get("work_mode") or "unknown")
    with c2:
        st.markdown(f"**Work mode:** `{wm}`")
    miles = prefs.get("distance_miles_from_19107")
    with c3:
        if wm == "remote":
            st.markdown("**Distance:** remote / N/A")
        elif miles is not None:
            try:
                st.markdown(f"**Distance:** ~{float(miles):.0f} mi from 19107")
            except (TypeError, ValueError):
                st.markdown("**Distance:** unknown")
        else:
            st.markdown("**Distance:** unknown")

    sal_low = prefs.get("salary_low_usd")
    floor = prefs.get("salary_floor_applied")
    try:
        floor_int = int(floor or 0)
    except (TypeError, ValueError):
        floor_int = 0
    if sal_low is not None or floor_int > 0:
        offered = _usd_compact(sal_low) if sal_low is not None else "not parsed"
        fl = _usd_compact(floor) if floor_int > 0 else "—"
        st.caption(f"Salary signal: {offered} offered / {fl} floor (`{wm}`)")

    boosts = prefs.get("boost_signals") if isinstance(prefs.get("boost_signals"), list) else []
    if boosts:
        st.markdown("**Boost signals:**")
        st.caption(", ".join(str(b) for b in boosts[:14]))

    notes = prefs.get("preference_notes") if isinstance(prefs.get("preference_notes"), list) else []
    if notes:
        with st.expander("why these preferences fired"):
            for n in notes[:24]:
                st.write(f"- {n}")


def _format_filter_reason(reason: str) -> str:
    if not reason:
        return ""
    pretty = {
        "junk_or_noise": "Junk / noise listing",
        "low_fit_or_pass": "Low fit + verdict=pass",
        "low_combined_score": "Combined score under threshold",
        "auto_closed": "Auto-closed",
        "duplicate_company_title": "Duplicate posting (same company + title as a newer row)",
        "test_fixture": "Smoke-test fixture (GateTestCo) — closed by cleanup script",
    }.get(reason)
    if pretty:
        return pretty
    if reason.startswith("location_policy:"):
        code = reason.split(":", 1)[1]
        return f"Location policy: {code.replace('_', ' ')}"
    if reason.startswith("search_preferences:"):
        code = reason.split(":", 1)[1]
        return {
            "title_avoided": "Title is on the hard-reject list",
            "salary_below_floor": "Salary below floor for this work mode",
            "outside_metro": "Outside 30-mile radius of 19107",
            "noise_filtered": "JD matched a noise pattern (intern / 1099 / MLM / etc.)",
            "years_gap_too_wide": "JD requires more years of experience than your configured gap allows.",
            "reject": "Search preferences reject",
        }.get(code, f"Search preferences: {code.replace('_', ' ')}")
    if reason.startswith("salary_below_min_usd:"):
        return f"Salary below floor ({reason.split(':', 1)[1]})"
    if reason.startswith("yoe_cap_jd_min_"):
        return f"Years-of-experience cap: {reason}"
    return reason


_SOURCE_LABELS = {
    "indeed": "Indeed",
    "usajobs": "USAJobs",
    "manual": "Manual",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "hn_whoishiring": "HN Who's Hiring",
    "remoteok": "RemoteOK",
    "arbeitnow": "Arbeitnow",
    "remotive": "Remotive",
    "themuse": "The Muse",
    "jobicy": "Jobicy",
    "working_nomads": "Working Nomads",
    "unknown": "Unknown",
}


def _source_label(source: str) -> str:
    src = (source or "").strip()
    if not src:
        return "Unknown"
    if src.startswith("jobspy_"):
        site = src[len("jobspy_") :].replace("_", " ")
        return f"{site.title()} (JobSpy)"
    if src.startswith("wwr_rss:"):
        return "We Work Remotely"
    return _SOURCE_LABELS.get(src, src.replace("_", " ").title())


def _source_badge_html(source: str) -> str:
    label = html.escape(_source_label(source))
    return (
        "<span style='background:#f3e8ff;color:#6b21a6;padding:2px 8px;border-radius:6px;"
        f"font-size:11px;font-weight:600;white-space:nowrap;'>{label}</span>"
    )


def _render_source_filter_bar(
    svc,
    *,
    status: str = "",
    statuses: Optional[List[str]] = None,
    session_key: str,
    min_list_rank: Optional[float] = None,
) -> Optional[str]:
    """Clickable source filters; returns active source slug or None for all."""
    if statuses:
        out = svc.svc_source_counts("", statuses=statuses, min_list_rank=min_list_rank)
    else:
        out = svc.svc_source_counts(status, min_list_rank=min_list_rank)
    sources = out.get("sources") or []
    active = (st.session_state.get(session_key) or "").strip()

    if not sources:
        st.session_state[session_key] = ""
        return None

    total = int(out.get("total") or 0)
    options: List[tuple[str, str]] = [("", f"All ({total})")]
    for row in sources:
        src = str(row.get("source") or "")
        cnt = int(row.get("count") or 0)
        options.append((src, f"{_source_label(src)} ({cnt})"))

    st.markdown("**Filter by source**")
    per_row = 5
    for i in range(0, len(options), per_row):
        chunk = options[i : i + per_row]
        cols = st.columns(len(chunk))
        for col, (src_val, label) in zip(cols, chunk):
            with col:
                if st.button(
                    label,
                    key=f"{session_key}_btn_{src_val or 'all'}_{i}",
                    type="primary" if active == src_val else "secondary",
                    use_container_width=True,
                ):
                    st.session_state[session_key] = src_val
                    _rerun()

    if active:
        st.caption(f"Showing **{_source_label(active)}** only.")
    return active or None


def _fmt_num(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return str(x)


def _render_score_chain_md(c: Dict[str, Any]) -> str:
    """Markdown showing how the final score was built."""
    inputs_bits = [
        f"Model `{_fmt_num(c.get('fit_model'))}`",
        f"Heuristic `{_fmt_num(c.get('fit_heuristic'))}`",
    ]
    ats = c.get("ats") if isinstance(c.get("ats"), dict) else {}
    if ats.get("ats_score") is not None:
        inputs_bits.append(f"ATS `{_fmt_num(ats.get('ats_score'))}`")
    inputs_line = " · ".join(inputs_bits)

    chain: List[str] = [f"Base **{_fmt_num(c.get('fit_base'))}**"]
    domain = c.get("domain") if isinstance(c.get("domain"), dict) else {}
    dm = domain.get("domain_multiplier")
    if dm is not None:
        chain.append(f"× domain `{_fmt_num(dm)}` → **{_fmt_num(c.get('fit_after_domain'))}**")
    sm = c.get("seniority_mult")
    if sm is not None:
        chain.append(f"× seniority `{_fmt_num(sm)}` → **{_fmt_num(c.get('fit_after_seniority'))}**")
    loc = c.get("loc") if isinstance(c.get("loc"), dict) else {}
    loc_action = (loc.get("action") or "").lower()
    lm = loc.get("multiplier")
    if loc_action == "reject":
        chain.append("× location **REJECT** → `0.00`")
    elif lm is not None:
        chain.append(f"× location `{_fmt_num(lm)}` → **{_fmt_num(c.get('fit_after_location'))}**")

    prefs = c.get("prefs") if isinstance(c.get("prefs"), dict) else {}
    pm = prefs.get("pref_multiplier")
    eff = prefs.get("effective_multiplier_applied")
    if pm is not None:
        chain.append(f"× prefs `{_fmt_num(pm)}` (effective `{_fmt_num(eff)}`) → **{_fmt_num(c.get('fit_final'))}**")

    return f"{inputs_line}  \n{' '.join(chain)}"


def _resume_label_map(assets: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for r in assets.get("resumes") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "").strip()
        if not rid:
            continue
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        friendly = (
            str(r.get("name") or "").strip()
            or str(r.get("label") or "").strip()
            or str(r.get("description") or "").strip()
            or str(meta.get("name") or "").strip()
            or str(meta.get("label") or "").strip()
            or str(meta.get("description") or "").strip()
            or str(meta.get("summary") or "").strip()
        )
        out[rid] = _truncate(friendly, 60) if friendly else rid
    return out


svc, assets_fn = _get_services()

st.title("Job Application Copilot")
st.caption("Pipeline-only dashboard: ingest, summarize, review queue, package, and outcomes.")

schema = svc.ensure_schema()
if not schema.get("ok"):
    err = str(schema.get("error") or "")
    st.error(
        f"**Cannot connect to Postgres.** `{err}`\n\n"
        "Check **`POSTGRES_HOST`**, **`POSTGRES_PORT`**, **`POSTGRES_USER`**, **`POSTGRES_PASSWORD`**, **`POSTGRES_DB`** "
        "in your `.env` (repo root). They must match the server you are running.\n\n"
        "- **Docker via `launch_all.ps1`** sets Postgres on host port **5433** with password **`yourpassword`** unless your `.env` overrides those.\n"
        "- **Local Postgres on 5432** needs the real password for user `postgres`.\n\n"
        "Restart Streamlit after editing `.env`."
    )
    if "password authentication failed" in err.lower():
        st.info(
            "If your DB runs in Docker but `.env` still says port **5432**, switch **`POSTGRES_PORT=5433`** "
            "(or whatever `docker port postgres-ai 5432` prints) and align **`POSTGRES_PASSWORD`** with the container."
        )
    st.stop()

with st.sidebar:
    try:
        from job_pipeline.rendercv_export import ENGINE_BUILD as _ENGINE_BUILD
    except Exception:
        _ENGINE_BUILD = "unknown (old code loaded)"
    st.caption(f"⚙ engine build: `{_ENGINE_BUILD}`")
    st.markdown("### Search preferences (`search_preferences.md`)")
    st.caption("Hand-edited authority for SEARCH + SCORING (not resume tailoring).")
    try:
        from job_pipeline.search_preferences import load_search_preferences

        if st.button("Reload", key="sidebar_prefs_reload"):
            load_search_preferences(reload=True)
            st.success("Parser cache cleared.")
        _pref_loaded = load_search_preferences()
        st.json(
            {
                "salary_floors": _pref_loaded.get("salary_floors"),
                "metro_radius_miles": _pref_loaded.get("metro_radius_miles"),
                "search_term_seeds": _pref_loaded.get("search_term_seeds"),
            }
        )
    except Exception as ex:
        st.warning(f"Could not load search preferences: {ex}")

    st.divider()
    with st.expander("Autofill extension (Firefox)", expanded=False):
        st.caption(
            "One-click fill on Indeed / iCIMS / Greenhouse application forms. "
            "Install once, sync profile from pipeline. "
            "References: edit `job_pipeline/references.json`."
        )
        if st.button("Write autofill profile JSON", key="sidebar_autofill_write", use_container_width=True):
            out = svc.svc_write_autofill_profile()
            if out.get("ok"):
                st.session_state["autofill_write_path"] = out.get("path")
                st.success(f"Wrote `{out.get('path')}`")
            else:
                st.error(out.get("error") or "write failed")
        _af_path = Path(__file__).resolve().parent / "job_pipeline" / "autofill_profile.json"
        if _af_path.is_file():
            st.download_button(
                "Download autofill_profile.json",
                data=_af_path.read_bytes(),
                file_name="autofill_profile.json",
                mime="application/json",
                key="sidebar_autofill_dl",
                use_container_width=True,
            )
        st.markdown(
            "**Install (Firefox)**  \n"
            "1. Open `about:debugging` → *This Firefox* → **Load Temporary Add-on**  \n"
            "2. Pick `browser_extension/manifest.json` in this repo  \n"
            "3. On an application page: extension icon → **Sync profile** (API on port 8000) "
            "or import JSON in **Options**  \n"
            "4. Click **Fill this application**"
        )

    st.divider()
    with st.expander("Danger zone (clear non-completed jobs)", expanded=False):
        st.caption("Wipe the active queue. Completed applications are preserved.")
        _render_clear_all_jobs_panel(svc, key_prefix="sidebar_danger")


def _summarize_eta_label(elapsed: float, done: int, total: int) -> str:
    if done <= 0 or elapsed < 1.0 or total <= done:
        return ""
    rate = done / elapsed
    secs = int((total - done) / rate)
    if secs < 60:
        return f" · ~{secs}s left"
    return f" · ~{secs // 60}m {secs % 60}s left"


def _run_ingest_with_progress() -> Dict[str, Any]:
    from job_pipeline.ingest import plan_ingest_steps

    steps = plan_ingest_steps()
    n_steps = max(1, len(steps))
    progress_bar = st.progress(0.0, text="Ingest: starting…")
    status_line = st.empty()
    started_at = time.time()

    def _on_progress(fraction: float, label: str, stats: Dict[str, int]) -> None:
        elapsed = time.time() - started_at
        touched = sum(
            int(v) for k, v in stats.items() if k.endswith("_jobs_touched")
        )
        step_i = min(n_steps, max(0, int(fraction * n_steps)))
        eta = _summarize_eta_label(elapsed, step_i, n_steps)
        progress_bar.progress(
            min(1.0, fraction),
            text=f"Ingest: {label} ({step_i}/{n_steps}){eta}",
        )
        status_line.caption(
            f"**{label}** · {touched} new jobs touched · {elapsed:.0f}s elapsed"
        )

    out = svc.svc_ingest(on_progress=_on_progress)
    elapsed = time.time() - started_at
    touched = sum(
        int(out.get(k) or 0)
        for k in out
        if k.endswith("_jobs_touched")
    )
    err_n = len(out.get("errors") or [])
    progress_bar.progress(
        1.0,
        text=f"Ingest: finished — {touched} jobs touched in {elapsed:.0f}s",
    )
    if err_n:
        status_line.warning(
            f"Finished in {elapsed:.0f}s with {err_n} source error(s). See **ingest_out** below."
        )
    else:
        status_line.caption(f"Done in {elapsed:.0f}s — {touched} new jobs touched across all sources.")
    return out


top1, top2, top3 = st.columns(3)
with top1:
    if st.button("Ingest Jobs", use_container_width=True):
        st.session_state["ingest_out"] = _run_ingest_with_progress()
        _rerun()
with top2:
    if st.button("Summarize New Jobs", use_container_width=True):
        st.session_state["summ_out"] = svc.svc_summarize(limit=25)
with top3:
    if st.button("Daily Run", use_container_width=True):
        st.session_state["daily_out"] = svc.svc_daily_run(
            ingest=True,
            summarize_limit=50,
            summarize_drain=True,
        )

fr1, fr2 = st.columns([2, 3])
with fr1:
    if st.button("Verify Freshness (live re-check)", use_container_width=True):
        with st.spinner("Re-fetching live postings; closing dead / onsite / non-US jobs…"):
            st.session_state["fresh_out"] = svc.svc_verify_freshness(limit=200)
        _rerun()
with fr2:
    st.caption(
        "Opens each pending posting and closes expired, onsite-outside-Philly, "
        "or non-US-only jobs. Indeed/LinkedIn block bots → left as 'verify at apply'."
    )

for key in ("ingest_out", "summ_out", "daily_out", "fresh_out"):
    if st.session_state.get(key):
        st.json(st.session_state[key])

_pipe_stats = _cached_pipeline_stats(_cache_token())
_closed_breakdown = _cached_closed_breakdown(_cache_token())


def _make_summarize_progress_callback(
    progress_bar,
    status_line,
    *,
    label: str,
    overall_done: int = 0,
    overall_total: int,
    started_at: float,
) -> Callable[[int, int, int, bool, str], None]:
    """Update Streamlit widgets after each job in a summarize batch."""

    def _on_progress(i: int, batch_total: int, item_id: int, ok: bool, msg: str) -> None:
        elapsed = time.time() - started_at
        overall_i = overall_done + i
        overall_frac = min(1.0, overall_i / max(1, overall_total))
        eta = _summarize_eta_label(elapsed, overall_i, overall_total)
        progress_bar.progress(
            overall_frac,
            text=(
                f"{label}: {overall_i}/{overall_total} "
                f"(this batch {i}/{batch_total}){eta}"
            ),
        )
        short = (msg or ("ok" if ok else "failed"))[:100]
        status_line.caption(
            f"Item **{item_id}**: {short} · {elapsed:.0f}s elapsed"
        )

    return _on_progress


def _run_summarize_batch_with_progress(
    limit: int,
    *,
    label: str = "Summarize",
    overall_done: int = 0,
    overall_total: Optional[int] = None,
) -> Dict[str, Any]:
    batch_n = max(1, min(int(limit), int(_pipe_stats.get("ingested") or limit or 1)))
    total = max(1, int(overall_total or batch_n))
    progress_bar = st.progress(0.0, text=f"{label}: starting…")
    status_line = st.empty()
    started_at = time.time()
    out = svc.svc_summarize(
        limit=batch_n,
        on_progress=_make_summarize_progress_callback(
            progress_bar,
            status_line,
            label=label,
            overall_done=overall_done,
            overall_total=total,
            started_at=started_at,
        ),
    )
    elapsed = time.time() - started_at
    ok_n = len(out.get("summarized") or [])
    err_n = len(out.get("errors") or [])
    progress_bar.progress(
        1.0,
        text=f"{label}: finished — {ok_n} summarized in {elapsed:.0f}s",
    )
    if err_n:
        status_line.warning(f"Batch done with {err_n} error(s). See **summ_out** below.")
    else:
        status_line.caption(
            f"Done in {elapsed:.0f}s — "
            f"{int(out.get('pending_review_count') or 0)} to review, "
            f"{int(out.get('auto_filtered_count') or 0)} auto-filtered."
        )
    return out


def _run_summarize_drain_step() -> None:
    """Process one summarize batch; caller reruns until drain completes."""
    stats = st.session_state.setdefault(
        "summ_drain_stats",
        {"started_ingested": int(_pipe_stats.get("ingested") or 0), "batches": 0},
    )
    ingested_now = int(svc.svc_pipeline_stats().get("ingested") or 0)
    if ingested_now <= 0 or st.session_state.get("summ_drain_stop"):
        st.session_state["summ_drain_active"] = False
        st.session_state["summ_out"] = stats.get("last_batch") or stats
        return
    if stats["batches"] >= int(st.session_state.get("summ_drain_max_batches") or 100):
        st.session_state["summ_drain_active"] = False
        stats["stop_reason"] = "max_batches"
        st.session_state["summ_out"] = stats
        return
    started = max(1, int(stats.get("started_ingested") or ingested_now or 1))
    done_prior = max(0, started - ingested_now)
    batch_limit = int(st.session_state.get("summ_drain_batch_size") or 50)
    st.markdown("### Summarize ALL — in progress")
    batch = _run_summarize_batch_with_progress(
        batch_limit,
        label="Summarize ALL",
        overall_done=done_prior,
        overall_total=started,
    )
    stats["batches"] = int(stats.get("batches") or 0) + 1
    stats["summarized_total"] = int(stats.get("summarized_total") or 0) + len(batch.get("summarized") or [])
    stats["filtered_total"] = int(stats.get("filtered_total") or 0) + int(batch.get("auto_filtered_count") or 0)
    stats["review_total"] = int(stats.get("review_total") or 0) + int(batch.get("pending_review_count") or 0)
    stats["last_batch"] = batch
    st.session_state["summ_drain_stats"] = stats
    st.session_state["summ_out"] = batch

if st.session_state.get("summ_drain_active"):
    with st.container():
        _run_summarize_drain_step()
    if st.session_state.get("summ_drain_active"):
        _rerun()

tabs = st.tabs(["Queue", "Manual application", "Pipeline", "Package Ready", "Completed", "Analytics", "Learning Gaps", "Assets", "Add Job"])

with tabs[0]:
    st.subheader("Pending Review Queue")
    if _pipe_stats.get("ok"):
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Unsummarized", int(_pipe_stats.get("ingested") or 0))
        m2.metric("Pending review", int(_pipe_stats.get("pending_review") or 0))
        m3.metric("Auto-filtered", int(_pipe_stats.get("closed") or 0))
        m4.metric("Package ready", int(_pipe_stats.get("package_ready") or 0))
        m5.metric("Completed", int(_pipe_stats.get("completed") or 0))

        _ingested_n = int(_pipe_stats.get("ingested") or 0)
        if _ingested_n > 0 or st.session_state.get("summ_drain_active"):
            b1, b2, b3 = st.columns([1, 1, 1])
            with b1:
                if st.button(
                    f"Summarize next batch (up to {min(_ingested_n, 50) if _ingested_n else 50})",
                    key="summarize_backlog_btn",
                    disabled=bool(st.session_state.get("summ_drain_active")),
                ):
                    batch_lim = min(_ingested_n, 50)
                    st.session_state["summ_out"] = _run_summarize_batch_with_progress(
                        batch_lim,
                        label="Summarize batch",
                        overall_total=batch_lim,
                    )
                    _rerun()
            with b2:
                if st.button(
                    "Summarize ALL pending",
                    key="summarize_all_btn",
                    type="primary",
                    disabled=bool(st.session_state.get("summ_drain_active")) or _ingested_n <= 0,
                ):
                    st.session_state["summ_drain_active"] = True
                    st.session_state["summ_drain_stop"] = False
                    st.session_state["summ_drain_batch_size"] = 50
                    st.session_state["summ_drain_max_batches"] = 100
                    st.session_state["summ_drain_stats"] = {
                        "started_ingested": _ingested_n,
                        "batches": 0,
                    }
                    _rerun()
            with b3:
                if st.session_state.get("summ_drain_active"):
                    if st.button("Stop summarizing", key="summ_drain_stop_btn"):
                        st.session_state["summ_drain_stop"] = True
                        st.session_state["summ_drain_active"] = False
                        _rerun()
            if st.session_state.get("summ_drain_active"):
                stats = st.session_state.get("summ_drain_stats") or {}
                started = max(1, int(stats.get("started_ingested") or _ingested_n or 1))
                done = started - int(svc.svc_pipeline_stats().get("ingested") or 0)
                st.caption(
                    f"**Summarize ALL** running above — batch {stats.get('batches', 0)} complete | "
                    f"~{done}/{started} processed | "
                    f"+{stats.get('review_total', 0)} to queue | "
                    f"{stats.get('filtered_total', 0)} filtered"
                )
            elif _ingested_n > 0:
                st.info(
                    f"{_ingested_n} jobs are ingested but not summarized. "
                    "Use **Summarize ALL pending** to drain the backlog in one click."
                )

        if _closed_breakdown.get("ok") and int(_closed_breakdown.get("closed_total") or 0) > 0:
            by_cat = _closed_breakdown.get("by_category") or {}
            labels = {
                "search_preferences": "Search preferences",
                "threshold": "Score threshold",
                "location": "Location policy",
                "salary": "Salary gate",
                "junk": "Junk / noise",
                "other": "Other",
                "unknown": "Unknown",
            }
            parts = [
                f"{labels.get(k, k)}: {v}"
                for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])
            ]
            st.caption(
                f"Auto-filter breakdown ({_closed_breakdown.get('closed_total')} closed): "
                + " | ".join(parts[:8])
            )

    if st.session_state.get("last_package_result"):
        last = st.session_state.get("last_package_result") or {}
        if last.get("ok"):
            st.success(
                f"Built package for item #{last.get('item_id')}. "
                "It moved to Package Ready and no longer appears in Pending Review."
            )
            _ng = last.get("named_requirement_gaps") or []
            if _ng:
                st.warning(
                    "Named JD requirements not in profile (omitted from resume): "
                    + ", ".join(_ng[:8])
                )
        else:
            st.error(
                f"Build package failed for item #{last.get('item_id')}: {last.get('error') or 'unknown error'}"
            )
    min_rank = st.slider("Min list rank", 0.0, 2.0, 0.0, 0.05)
    limit = st.number_input(
        "Max items",
        5,
        100,
        int(st.session_state.get("queue_default_limit", 12)),
        5,
        help="Lower this if the dashboard feels sluggish. Streamlit re-renders every visible card on each interaction.",
    )
    _queue_source = _render_source_filter_bar(
        svc,
        status="pending_review",
        session_key="queue_source_filter",
        min_list_rank=float(min_rank),
    )
    # Lane tabs: each agent works one category. Deterministic assignment at
    # summarize time (job_pipeline/lane_category.py) — no SQL-lock claim races.
    _cat_counts = _cached_category_counts(
        _cache_token(), "pending_review", float(min_rank), _queue_source or ""
    )
    _cat_total = sum(_cat_counts.values()) if _cat_counts else 0
    _cat_options = ["All"] + list(CATEGORY_ORDER)
    _cat_labels = {
        "All": f"All ({_cat_total})",
        **{c: f"{category_label(c)} ({int(_cat_counts.get(c, 0))})" for c in CATEGORY_ORDER},
    }
    _queue_category_choice = st.radio(
        "Lane",
        _cat_options,
        index=0,
        horizontal=True,
        format_func=lambda c: _cat_labels.get(c, c),
        key="queue_category_filter",
        help="Point each agent at one lane. IT Help Desk = Tier 1, IT General = "
        "sysadmin/network/NOC, Operations = ops/coordination/CS, Remote = remote non-IT.",
    )
    _queue_category = None if _queue_category_choice == "All" else _queue_category_choice
    _pending_total = int((_pipe_stats or {}).get("pending_review") or 0)
    _pending_visible = (
        _cached_count_pending_review(_cache_token(), float(min_rank), _queue_source or "")
        if _pipe_stats.get("ok")
        else _pending_total
    )
    if _pending_total and _pending_visible < _pending_total:
        _hidden_note = f"**{_pending_total - _pending_visible} hidden** by the min rank slider"
        if _queue_source:
            _hidden_note += f" and/or **{_source_label(_queue_source)}** filter"
        st.info(
            f"**{_pending_visible} of {_pending_total}** pending-review jobs shown — "
            f"{_hidden_note}. "
            "Lower min rank to 0.0 or click **All** to see everything that passed filters."
        )
    out = svc.svc_queue(
        status="pending_review",
        limit=int(limit),
        min_list_rank=float(min_rank),
        with_card=True,
        source=_queue_source,
        category=_queue_category,
    )
    items = out.get("items") or []
    try:
        assets = assets_fn()
    except Exception:
        assets = {}
    resume_labels = _resume_label_map(assets)
    st.caption(f"{len(items)} items")
    for row in items:
        iid = int(row["item_id"])
        c = _card_data(row)
        title = row.get("title") or "(no title)"
        company = row.get("company_name") or ""
        fit = row.get("fit_score")
        why_preview = _truncate(_first_sentence(c["why"]), 120) or "No match rationale."
        resume_id = c["resume_id"] or "—"
        resume_label = resume_labels.get(c["resume_id"] or "", resume_id)

        badges = [
            _source_badge_html(row.get("source") or ""),
            _verdict_badge_html(c["verdict"]),
            _fit_badge_html(fit, fit_score_raw=c.get("fit_score_raw")),
            _salary_badge_html(c["salary"]),
            _ats_badge_html(c["ats"]),
            _loc_badge_html(c["loc"]),
            _seniority_badge_html(c["seniority_fit"], c["seniority_mult"]),
            _friction_badge_html(c["friction"]),
        ]
        badge_row = "".join(b for b in badges if b)
        headline_line = ""
        if c["headline"]:
            headline_line = (
                f"<div style='margin-top:4px;color:#111827;font-size:13px;font-weight:500;'>"
                f"{html.escape(_truncate(c['headline'], 140))}</div>"
            )

        reqs = c.get("key_requirements") or []
        reqs_line = ""
        if reqs:
            chips = "".join(
                f"<span style=\"background:#eff6ff;color:#1e3a8a;padding:2px 8px;"
                f"border-radius:6px;font-size:11px;font-weight:500;white-space:nowrap;"
                f"margin-right:4px;display:inline-block;\">{html.escape(str(r))}</span>"
                for r in reqs[:7]
            )
            reqs_line = f"<div style='margin-top:6px;'>{chips}</div>"

        st.markdown(
            (
                "<div style='border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin:8px 0;'>"
                f"<div style='display:flex;justify-content:space-between;gap:8px;align-items:flex-start;'>"
                f"<div><strong>#{iid} {html.escape(title)} @ {html.escape(company)}</strong></div>"
                f"<div style='display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;'>{badge_row}</div>"
                "</div>"
                f"{headline_line}"
                f"{reqs_line}"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        with st.expander(f"Details #{iid}"):
            st.write(
                f"**Source:** {_source_label(row.get('source') or '')}  |  "
                f"**Salary:** {c['salary']}  |  **Suggested resume:** `{resume_label}`"
            )
            st.write(f"**Why:** {c['why'] or '—'}")
            st.write(f"**Gaps:** {c['gaps'] or '—'}")

            if st.button("Check JD requirement gaps", key=f"gapcheck_{iid}"):
                from job_pipeline.resume_gaps import detect_gaps
                from job_pipeline.resume_tailor import _load_grounded_profile_text

                with st.spinner("Scanning JD vs profile…"):
                    st.session_state[f"queue_gaps_{iid}"] = detect_gaps(
                        row.get("description_text") or "",
                        profile_text=_load_grounded_profile_text(),
                        use_llm=False,
                    )
            _qg = st.session_state.get(f"queue_gaps_{iid}")
            if _qg:
                _render_jd_gaps_panel(_qg, key_prefix=f"queue_{iid}")

            st.markdown("**Why this score**")
            st.markdown(_render_score_chain_md(c))

            loc = c["loc"] or {}
            if loc:
                loc_bits = []
                if loc.get("classification"):
                    loc_bits.append(f"`{loc.get('classification')}`")
                if loc.get("action"):
                    loc_bits.append(f"action **{loc.get('action')}**")
                if loc.get("reason_code"):
                    loc_bits.append(f"_{loc.get('reason_code')}_")
                if loc_bits:
                    st.markdown("**Location policy:** " + " · ".join(loc_bits))

            ats = c["ats"] or {}
            ats_hits = ats.get("ats_keyword_hits") if isinstance(ats, dict) else None
            if ats_hits:
                st.markdown(
                    "**ATS keyword hits:** " + ", ".join(f"`{h}`" for h in ats_hits[:14])
                )

            if c["yoe_cap_note"]:
                st.warning(f"YOE cap applied: {c['yoe_cap_note']}")

            if c["seniority_notes"] and c["seniority_mult"] is not None:
                try:
                    if abs(float(c["seniority_mult"]) - 1.0) > 0.02:
                        st.caption(
                            f"Seniority signals: {c['seniority_notes']} → ×{float(c['seniority_mult']):.2f}"
                        )
                except (TypeError, ValueError):
                    pass

            domain = c["domain"] or {}
            domain_reasons = domain.get("reasons") if isinstance(domain, dict) else None
            if domain_reasons:
                with st.expander("Domain fit reasons"):
                    for r in domain_reasons[:10]:
                        st.write(f"- {r}")

            st.markdown("**Search preferences**")
            _render_prefs_streamlit(c.get("prefs") or {})

            if c["junk"]:
                st.error(f"Flagged junk: {c['junk_reason'] or '(no reason)'}")

            apply_url = (row.get("apply_url") or row.get("job_url") or "").strip()
            if apply_url:
                st.markdown(f"[Open posting]({apply_url})")
            b1, b2, b3, b4 = st.columns(4)
            with b1:
                if st.button("Approve", key=f"approve_{iid}"):
                    st.session_state[f"msg_{iid}"] = svc.svc_decide(iid, "approve")
                    _rerun()
            with b2:
                if st.button("Skip", key=f"skip_{iid}"):
                    st.session_state[f"msg_{iid}"] = svc.svc_decide(iid, "skip")
                    _rerun()
            with b3:
                if st.button("Needs edits", key=f"edits_{iid}"):
                    st.session_state[f"msg_{iid}"] = svc.svc_decide(iid, "needs_edits")
                    _rerun()
            with b4:
                if st.button("Build package", key=f"pkg_{iid}"):
                    svc.svc_decide(iid, "approve")
                    with st.spinner(f"Building #{iid} — tailoring resume + cover letter…"):
                        pkg_out = svc.svc_build_package(iid)
                    st.session_state[f"msg_{iid}"] = pkg_out
                    st.session_state["last_package_result"] = {
                        "item_id": iid,
                        "ok": bool(pkg_out.get("ok")),
                        "error": pkg_out.get("error") or "",
                        "named_requirement_gaps": pkg_out.get("named_requirement_gaps") or [],
                    }
                    if pkg_out.get("ok"):
                        _record_recent_build(iid)
                    _rerun()
            if st.session_state.get(f"msg_{iid}"):
                st.json(st.session_state[f"msg_{iid}"])

with tabs[2]:
    st.subheader("Status Board")
    statuses = [
        "pending_review",
        "drafted",
        "approved",
        "package_ready",
        "submitted",
        "responded",
        "rejected",
        "closed",
    ]
    for status in statuses:
        need_cards = status == "closed"
        order_by = "built_at" if status == "package_ready" else "rank"
        rows = (
            svc.svc_queue(status=status, limit=40, with_card=need_cards, order_by=order_by)
            .get("items")
            or []
        )
        if status == "package_ready":
            rows = svc.sort_items_recent_first(
                rows, st.session_state.get("recent_built_ids") or []
            )
        with st.expander(f"{status} ({len(rows)})", expanded=status in ("pending_review", "package_ready")):
            if status == "closed" and rows:
                buckets: Dict[str, List[Dict[str, Any]]] = {}
                for r in rows[:40]:
                    cd = _card_data(r)
                    sj = r.get("summary_json") or {}
                    if isinstance(sj, str):
                        try:
                            sj = json.loads(sj)
                        except Exception:
                            sj = {}
                    cat = (sj.get("close_reason_category") or "").strip()
                    raw_fr = (cd.get("filter_reason") or sj.get("filter_reason") or "").strip()
                    if cat:
                        bucket_key = cat
                    elif raw_fr:
                        bucket_key = raw_fr
                    else:
                        bucket_key = "(no reason recorded)"
                    buckets.setdefault(bucket_key, []).append(r)
                for raw_fr, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
                    label = _format_filter_reason(raw_fr) if raw_fr.startswith(("search_", "location_", "junk", "low_", "salary", "yoe")) else {
                        "search_preferences": "Search preferences (soft-rank only when honor_auto_close=false)",
                        "threshold": "Score threshold",
                        "location": "Location policy",
                        "salary": "Salary gate",
                        "junk": "Junk / noise",
                        "other": "Other",
                    }.get(raw_fr, raw_fr if raw_fr != "(no reason recorded)" else raw_fr)
                    if label == raw_fr and raw_fr not in ("(no reason recorded)",):
                        label = _format_filter_reason(raw_fr) or raw_fr.replace("_", " ")
                    if raw_fr == "(no reason recorded)":
                        label = raw_fr
                    st.markdown(f"**{label}** ({len(items)})")
                    for r in items:
                        cd = _card_data(r)
                        loc = cd["loc"] or {}
                        loc_tag = ""
                        if loc.get("classification"):
                            loc_tag = f" · _{loc.get('classification')}_"
                    st.write(
                        f"  - #{r.get('item_id')} {r.get('title')} @ {r.get('company_name')}"
                        f" · {_source_label(r.get('source') or '')}{loc_tag}"
                    )
            else:
                for r in rows[:40]:
                    prefix = ""
                    if status == "package_ready":
                        recent = st.session_state.get("recent_built_ids") or []
                        if recent and int(r.get("item_id") or 0) == int(recent[0]):
                            prefix = "🆕 "
                        elif int(r.get("item_id") or 0) in recent[:5]:
                            prefix = "• "
                    st.write(
                        f"{prefix}#{r.get('item_id')} - {r.get('title')} @ {r.get('company_name')}"
                        f" · {_source_label(r.get('source') or '')}"
                    )

with tabs[3]:
    st.subheader("Package Ready")
    _last_pkg = st.session_state.get("last_package_result") or {}
    if _last_pkg:
        if _last_pkg.get("ok"):
            st.info(f"Most recent package build: item #{_last_pkg.get('item_id')}.")
        else:
            st.error(
                f"Last rebuild failed for item #{_last_pkg.get('item_id')}: "
                f"{_last_pkg.get('error') or 'unknown error'}"
            )
            if str(_last_pkg.get("error") or "").startswith("json_parse_failed"):
                st.caption(
                    "The writing model returned a response the parser could not read as JSON. "
                    "The pipeline retries automatically; click Rebuild package again. "
                    "If it persists, try `OPENAI_WRITING_MODEL=gpt-4o` or "
                    "`LLM_WRITING_PROVIDER=gemini` with `GEMINI_RESUME_TAILOR_MODEL=models/gemini-2.5-flash`."
                )
    _pkg_source = _render_source_filter_bar(
        svc,
        status="package_ready",
        session_key="package_source_filter",
    )
    rows = (
        svc.svc_queue(
            status="package_ready",
            limit=30,
            order_by="built_at",
            source=_pkg_source,
        ).get("items")
        or []
    )
    rows = svc.sort_items_recent_first(rows, st.session_state.get("recent_built_ids") or [])
    recent_ids = st.session_state.get("recent_built_ids") or []
    if recent_ids:
        st.caption(
            "Recently built packages are pinned to the top (🆕 = just built this session)."
        )
    outcomes = ["interview", "offer", "rejection", "ghosted", "withdrawn", "unknown"]
    for r in rows:
        iid = int(r["item_id"])
        is_newest = recent_ids and int(recent_ids[0]) == iid
        is_recent = iid in recent_ids[:5]
        item = svc.svc_get_item(iid).get("item") or {}
        pkg_meta = item.get("package_meta") or {}
        if isinstance(pkg_meta, str):
            try:
                pkg_meta = json.loads(pkg_meta)
            except Exception:
                pkg_meta = {}
        mode_label = (pkg_meta.get("mode") or "both").replace("_", " ")
        title_prefix = "🆕 " if is_newest else ("• " if is_recent else "")
        with st.expander(
            f"{title_prefix}#{iid} {item.get('title')} @ {item.get('company_name')}"
            f" · {_source_label(item.get('source') or r.get('source') or '')}",
            expanded=is_newest or (st.session_state.get("last_rebuilt_iid") == iid),
        ):
            _sal_badge = _salary_badge_html(item.get("salary_text") or r.get("salary_text") or "")
            st.markdown(
                f"{_source_badge_html(item.get('source') or r.get('source') or '')} "
                f"{_sal_badge + ' ' if _sal_badge else ''}"
                f"<span style='background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:6px;font-size:12px;'>"
                f"mode: {html.escape(mode_label)}</span>",
                unsafe_allow_html=True,
            )
            if pkg_meta.get("gate_blocked"):
                st.error(
                    f"Gate blocked — judge {pkg_meta.get('judge_score', '?')}/100 "
                    f"(revisions: {pkg_meta.get('gate_revisions', 0)}). Review before submitting."
                )
                for jc in (pkg_meta.get("judge_critique") or [])[:5]:
                    st.caption(f"Judge: {jc}")
            elif pkg_meta.get("judge_score") is not None:
                st.caption(
                    f"Quality judge: {pkg_meta.get('judge_score')}/100 "
                    f"({pkg_meta.get('judge_verdict') or 'n/a'})"
                )
            pr1, pr2 = st.columns(2)
            with pr1:
                _r_pdf = pkg_meta.get("resume_pdf") or pkg_meta.get("resume_file")
                if _r_pdf and Path(_r_pdf).is_file():
                    st.download_button(
                        "⬇ Resume",
                        data=_cached_file_bytes(str(_r_pdf)),
                        file_name=Path(_r_pdf).name,
                        mime="application/octet-stream",
                        key=f"pkg_dl_resume_{iid}",
                    )
                elif _r_pdf:
                    st.caption(f"Resume (file not found): `{_r_pdf}`")
                else:
                    st.caption("No resume artifact.")
            with pr2:
                _c_pdf = pkg_meta.get("cover_pdf")
                if _c_pdf and Path(_c_pdf).is_file():
                    st.download_button(
                        "⬇ Cover letter",
                        data=_cached_file_bytes(str(_c_pdf)),
                        file_name=Path(_c_pdf).name,
                        mime="application/pdf",
                        key=f"pkg_dl_cover_{iid}",
                    )
                elif _c_pdf:
                    st.caption(f"Cover PDF (file not found): `{_c_pdf}`")
                else:
                    st.caption("No cover letter artifact.")
            apply_url = (item.get("apply_url") or item.get("job_url") or "").strip()
            if apply_url:
                st.markdown(f"[Open apply URL]({apply_url})")
            rb_mode = pkg_meta.get("mode") or "both"
            if st.button(
                "🔄 Rebuild package",
                key=f"rebuild_{iid}",
                help="Re-run tailoring with the current prompts/settings — regenerates the resume + cover letter for this job.",
            ):
                with st.spinner(f"Rebuilding #{iid} — re-tailoring resume + cover letter…"):
                    svc.svc_decide(iid, "approve")
                    rb_out = svc.svc_build_package(iid, mode=rb_mode, is_rebuild=True)
                st.session_state[f"rebuild_msg_{iid}"] = rb_out
                st.session_state["last_package_result"] = {
                    "item_id": iid,
                    "ok": bool(rb_out.get("ok")),
                    "error": rb_out.get("error") or "",
                    "named_requirement_gaps": rb_out.get("named_requirement_gaps") or [],
                }
                st.session_state["last_rebuilt_iid"] = iid
                if rb_out.get("ok"):
                    _record_recent_build(iid)
                _rerun()
            _rb_msg = st.session_state.get(f"rebuild_msg_{iid}")
            if _rb_msg:
                if _rb_msg.get("ok"):
                    st.success("Rebuilt with the latest settings — download the refreshed docs above.")
                    _pkg_w = (pkg_meta.get("warnings") or []) if isinstance(pkg_meta, dict) else []
                    _resume_missing = not (
                        (pkg_meta.get("resume_pdf") or pkg_meta.get("resume_file"))
                        and Path(str(pkg_meta.get("resume_pdf") or pkg_meta.get("resume_file") or "")).is_file()
                    )
                    if rb_mode != "cover_letter_only" and _resume_missing:
                        st.warning(
                            "Cover letter rebuilt, but **resume PDF is missing**. "
                            "Usually RenderCV rejected experience dates — rebuild again after the fix, "
                            "or check package warnings below."
                        )
                    for w in _pkg_w[:6]:
                        if w:
                            st.caption(f"⚠ {w}")
                else:
                    st.error(f"Rebuild failed: {_rb_msg.get('error') or 'unknown error'}")
            st.text_area("Cover letter", value=(item.get("cover_letter_text") or "")[:8000], height=220, key=f"cl_{iid}")
            o1, o2 = st.columns(2)
            with o1:
                if st.button("Mark submitted", key=f"submitted_{iid}"):
                    st.session_state[f"out_{iid}"] = svc.svc_decide(iid, "mark_submitted")
                    _rerun()
            with o2:
                pick = st.selectbox("Outcome", outcomes, key=f"pick_{iid}")
                if st.button("Save outcome", key=f"save_{iid}"):
                    st.session_state[f"out_{iid}"] = svc.svc_record_outcome(iid, pick, "")
                    _rerun()
            if st.session_state.get(f"out_{iid}"):
                st.json(st.session_state[f"out_{iid}"])

with tabs[4]:
    from job_pipeline.states import COMPLETED_STATUSES

    st.subheader("Completed applications")
    st.caption(
        "Jobs marked **submitted** (applied), plus **responded** and **rejected**. "
        "These records are **never removed** by Clear all jobs in the sidebar."
    )
    _completed_total = _cached_count_completed(_cache_token())
    st.metric("Completed applications", _completed_total)

    _completed_source = _render_source_filter_bar(
        svc,
        statuses=sorted(COMPLETED_STATUSES),
        session_key="completed_source_filter",
    )
    _completed_limit = st.number_input(
        "Max items",
        5,
        200,
        50,
        5,
        key="completed_list_limit",
    )
    _completed_rows = (
        svc.svc_list_completed(limit=int(_completed_limit), source=_completed_source).get("items") or []
    )
    st.caption(f"Showing {len(_completed_rows)} item(s)")
    _completed_outcomes = ["interview", "offer", "rejection", "ghosted", "withdrawn", "unknown"]

    for row in _completed_rows:
        iid = int(row["item_id"])
        title = row.get("title") or "(no title)"
        company = row.get("company_name") or ""
        status = row.get("status") or ""
        applied = row.get("applied_at") or ""
        outcome = row.get("outcome") or "—"
        src_label = _source_label(row.get("source") or "")

        with st.expander(f"#{iid} {title} @ {company} · {status} · {outcome}"):
            _sal_badge = _salary_badge_html(row.get("salary_text") or "")
            st.markdown(
                f"{_source_badge_html(row.get('source') or '')} {_sal_badge + ' ' if _sal_badge else ''}"
                f"**Applied:** {applied or '—'} · **Source:** {src_label} · **Status:** `{status}` · **Outcome:** {outcome}",
                unsafe_allow_html=True,
            )
            apply_url = (row.get("apply_url") or row.get("job_url") or "").strip()
            if apply_url:
                st.markdown(f"[Open posting]({apply_url})")

            item = svc.svc_get_item(iid).get("item") or row
            pkg_meta = item.get("package_meta") or {}
            if isinstance(pkg_meta, str):
                try:
                    pkg_meta = json.loads(pkg_meta)
                except Exception:
                    pkg_meta = {}

            dl1, dl2 = st.columns(2)
            with dl1:
                _r_pdf = pkg_meta.get("resume_pdf") or pkg_meta.get("resume_file")
                if _r_pdf and Path(_r_pdf).is_file():
                    st.download_button(
                        "Download resume",
                        data=_cached_file_bytes(str(_r_pdf)),
                        file_name=Path(_r_pdf).name,
                        mime="application/octet-stream",
                        key=f"completed_dl_resume_{iid}",
                    )
                elif _r_pdf:
                    st.caption(f"Resume path (missing file): `{_r_pdf}`")
            with dl2:
                _c_pdf = pkg_meta.get("cover_pdf")
                if _c_pdf and Path(_c_pdf).is_file():
                    st.download_button(
                        "Download cover letter",
                        data=_cached_file_bytes(str(_c_pdf)),
                        file_name=Path(_c_pdf).name,
                        mime="application/octet-stream",
                        key=f"completed_dl_cover_{iid}",
                    )
                elif _c_pdf:
                    st.caption(f"Cover letter path (missing file): `{_c_pdf}`")

            o1, o2 = st.columns(2)
            with o1:
                pick = st.selectbox(
                    "Update outcome",
                    _completed_outcomes,
                    index=(
                        _completed_outcomes.index(outcome)
                        if outcome in _completed_outcomes
                        else len(_completed_outcomes) - 1
                    ),
                    key=f"completed_outcome_{iid}",
                )
                if st.button("Save outcome", key=f"completed_save_outcome_{iid}"):
                    st.session_state[f"completed_out_{iid}"] = svc.svc_record_outcome(iid, pick, "")
                    _rerun()
            with o2:
                if st.button("Mark responded", key=f"completed_responded_{iid}"):
                    st.session_state[f"completed_out_{iid}"] = svc.svc_decide(iid, "mark_responded")
                    _rerun()
            if st.session_state.get(f"completed_out_{iid}"):
                st.json(st.session_state[f"completed_out_{iid}"])

with tabs[5]:
    st.subheader("Analytics")
    an = svc.svc_analytics()
    st.json(an)

with tabs[6]:
    st.subheader("Learning Gaps")
    st.caption(
        "JD requirements that appear in postings but aren't grounded in "
        "your `career_master.md` / `consolidated_profile.json`. Use this to "
        "pick what to study next (free certs, home lab, AI-assisted learning)."
    )
    try:
        from job_pipeline.learning_gaps import (
            top_gaps as _lg_top,
            category_counts as _lg_cats,
            mark_learned as _lg_mark_learned,
        )

        cats = _lg_cats()
        if cats:
            cat_cols = st.columns(min(6, max(1, len(cats))))
            for (cat, n), col in zip(sorted(cats.items(), key=lambda x: -x[1]), cat_cols):
                col.metric(cat.title(), n)
        else:
            st.info(
                "No learning gaps captured yet. Run a fresh ingest + summarize "
                "cycle and they'll start populating here."
            )

        gap_filter = st.selectbox(
            "Filter by category",
            options=["all", "cert", "tool", "skill", "framework", "other"],
            index=0,
            key="learning_gaps_filter",
        )
        gap_limit = st.slider(
            "Show top N", min_value=10, max_value=200, value=40, step=10,
            key="learning_gaps_limit",
        )
        rows = _lg_top(
            n=gap_limit,
            category=None if gap_filter == "all" else gap_filter,
        )
        if not rows and cats:
            st.caption("No gaps match the current filter.")
        for row in rows:
            with st.container():
                colA, colB, colC = st.columns([5, 1, 1])
                _cat = row.get("category") or "other"
                _cat_bg = {
                    "cert": "#fef3c7",
                    "tool": "#dbeafe",
                    "skill": "#dcfce7",
                    "framework": "#ede9fe",
                    "years": "#f3f4f6",
                    "other": "#f3f4f6",
                }.get(_cat, "#f3f4f6")
                _cat_fg = {
                    "cert": "#92400e",
                    "tool": "#1e40af",
                    "skill": "#166534",
                    "framework": "#5b21b6",
                    "years": "#374151",
                    "other": "#374151",
                }.get(_cat, "#374151")
                samples = row.get("samples") or []
                sample_str = ""
                if samples:
                    sample_str = " · ".join(
                        f"#{s.get('item_id')} {(s.get('title') or '')[:40]}"
                        for s in samples[:3]
                    )
                colA.markdown(
                    f"<div style='padding:6px 0;'>"
                    f"<span style='background:{_cat_bg};color:{_cat_fg};padding:2px 8px;"
                    f"border-radius:999px;font-size:11px;font-weight:600;margin-right:8px;'>"
                    f"{_cat}</span>"
                    f"<strong>{html.escape(row.get('display') or row.get('keyword'))}</strong>"
                    f"<span style='color:#6b7280;font-size:12px;margin-left:6px;'>"
                    f"({row.get('count')}x · last {row.get('last_seen','—')})</span>"
                    f"<div style='color:#9ca3af;font-size:11px;margin-top:2px;'>"
                    f"{html.escape(sample_str)}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                colB.write(f"**{row.get('count')}**")
                if colC.button("Ignore", key=f"lg_ignore_{row['keyword']}"):
                    _lg_mark_learned(row["keyword"])
                    _rerun()
    except Exception as e:
        st.error(f"Learning gaps panel failed: {e}")

with tabs[7]:
    st.subheader("Assets")
    try:
        assets = assets_fn()
    except Exception as e:
        st.error(str(e))
        assets = {}
    st.write("Resumes")
    for r in assets.get("resumes") or []:
        if isinstance(r, dict):
            st.write(f"- `{r.get('id')}` -> {r.get('path')}")
    st.write("Cover letter templates")
    for t in assets.get("cover_letter_templates") or []:
        if isinstance(t, dict):
            st.write(f"- `{t.get('id')}` -> {t.get('path') or '(inline text)'}")

with tabs[8]:
    with st.expander("Paste & extract from job posting (recommended)", expanded=True):
        st.caption(
            "Paste the full content of an Indeed/LinkedIn/company careers page below. "
            "Gemini will pull out the fields. You can edit anything it gets wrong before submitting."
        )
        pasted_job = st.text_area(
            "Paste job posting",
            height=260,
            key="add_job_paste_box",
            placeholder="Copy the entire posting from your browser and paste here...",
        )
        if st.button("Extract fields", key="add_job_extract_btn", type="primary"):
            if not (pasted_job or "").strip():
                st.warning("Paste something first.")
            else:
                from job_pipeline.job_extractor import extract_job_fields

                with st.spinner("Extracting fields with Gemini..."):
                    result = extract_job_fields(pasted_job)
                if result.get("error"):
                    st.error(f"Extraction failed: {result['error']}")
                else:
                    for k in (
                        "add_job_company_input",
                        "add_job_title_input",
                        "add_job_apply_url_input",
                        "add_job_location_input",
                        "add_job_salary_input",
                        "add_job_description_input",
                    ):
                        st.session_state.pop(k, None)

                    desc = (result.get("description") or "").strip()
                    jt = (result.get("job_type") or "").strip()
                    wm = (result.get("work_mode") or "").strip()
                    extras = []
                    if jt:
                        extras.append(f"Job type: {jt}")
                    if wm:
                        extras.append(f"Work mode: {wm}")
                    if extras:
                        desc = desc.rstrip() + "\n\n---\n" + "\n".join(extras)

                    st.session_state["add_job_company"] = result.get("company", "")
                    st.session_state["add_job_title"] = result.get("title", "")
                    st.session_state["add_job_apply_url"] = result.get("apply_url", "")
                    st.session_state["add_job_location"] = result.get("location", "")
                    st.session_state["add_job_salary"] = result.get("salary", "")
                    st.session_state["add_job_description"] = desc
                    st.success("Fields extracted. Review and edit below, then click Add.")
                    _rerun()

    st.subheader("Add Manual Job")
    with st.form("add_job"):
        company = st.text_input(
            "Company",
            value=st.session_state.get("add_job_company", ""),
            key="add_job_company_input",
        )
        title = st.text_input(
            "Title",
            value=st.session_state.get("add_job_title", ""),
            key="add_job_title_input",
        )
        apply_url = st.text_input(
            "Apply URL",
            value=st.session_state.get("add_job_apply_url", ""),
            key="add_job_apply_url_input",
            help="The URL of the job posting itself (e.g. indeed.com/viewjob?jk=...). Not the company profile.",
        )
        location = st.text_input(
            "Location",
            value=st.session_state.get("add_job_location", ""),
            key="add_job_location_input",
        )
        salary = st.text_input(
            "Salary",
            value=st.session_state.get("add_job_salary", ""),
            key="add_job_salary_input",
        )
        description = st.text_area(
            "Description",
            value=st.session_state.get("add_job_description", ""),
            height=160,
            key="add_job_description_input",
        )
        submit = st.form_submit_button("Add")
    if submit:
        out = svc.svc_manual_add(
            company_name=company,
            title=title,
            apply_url=apply_url,
            description_text=description,
            location=location,
            salary_text=salary,
        )
        st.json(out)
        for k in (
            "add_job_company",
            "add_job_title",
            "add_job_apply_url",
            "add_job_location",
            "add_job_salary",
            "add_job_description",
            "add_job_company_input",
            "add_job_title_input",
            "add_job_apply_url_input",
            "add_job_location_input",
            "add_job_salary_input",
            "add_job_description_input",
        ):
            st.session_state.pop(k, None)

with tabs[1]:
    st.subheader("Manual application")
    repo_root = _REPO_ROOT
    from job_pipeline.bootstrap_resume_profile import (
        consolidated_profile_stale_warning,
        load_consolidated_profile,
    )
    from job_pipeline.resume_tailor import tailor_resume_from_jd, _load_grounded_profile_text
    from job_pipeline.resume_gaps import answers_to_extra_facts, detect_gaps
    from job_pipeline.rendercv_export import render_tailored_resume_pdf
    from job_pipeline.service import build_application_artifacts
    from application_assets import get_default_apply_asset_ids

    with st.expander("Preferences debug", expanded=False):
        dbg_title = st.text_input("Title", key="prefs_dbg_title")
        dbg_loc = st.text_input("Location", key="prefs_dbg_loc")
        dbg_salary = st.text_input("Salary text", key="prefs_dbg_salary")
        dbg_src = st.text_input("Source", value="", key="prefs_dbg_src", help="e.g. usajobs, indeed")
        dbg_body = st.text_area("JD body", height=160, key="prefs_dbg_body")
        if st.button("Score with search_preferences", key="prefs_dbg_run"):
            from job_pipeline.search_preferences import score_posting_against_preferences

            out = score_posting_against_preferences(
                {
                    "title": dbg_title,
                    "location": dbg_loc,
                    "salary_text": dbg_salary,
                    "description_text": dbg_body,
                    "source": dbg_src,
                }
            )
            ac = out.get("auto_close_reason")
            if ac:
                st.error(_format_filter_reason(f"search_preferences:{ac}"))
            st.json(out)

    stale_w = consolidated_profile_stale_warning()
    if stale_w:
        st.warning(stale_w)

    schema_mr = svc.ensure_schema()
    pg_mr_ok = bool(schema_mr.get("ok"))
    if not pg_mr_ok:
        st.caption("Postgres schema not ready — tailoring still works, but gap answers will not persist.")

    profile_txt = _load_grounded_profile_text()
    if len(profile_txt.strip()) < 200:
        st.markdown(
            "Build a single grounded master profile from the PDF resumes in `./resume/` before tailoring."
        )
        if st.button("Bootstrap Profile", type="primary", key="mr_btn_bootstrap"):
            from job_pipeline.genai_settings import google_api_key

            if not google_api_key().strip():
                st.error(
                    "Missing Gemini API key. Add **`GEMINI_API_KEY`** or **`GOOGLE_API_KEY`** to your `.env` "
                    "file at the repo root, restart Streamlit, then try again."
                )
            else:
                try:
                    from job_pipeline.bootstrap_resume_profile import run_bootstrap

                    with st.spinner("Consolidating resume PDFs with Gemini (often 30–60 seconds)..."):
                        result = run_bootstrap()
                    if result.get("ok"):
                        n = len(result.get("source_files_consolidated") or [])
                        st.success(
                            f"Consolidated **{n}** resume PDF(s). Master profile saved to "
                            "`job_pipeline/consolidated_profile.md` (and `.json`)."
                        )
                        _rerun()
                    else:
                        st.error(result.get("error") or "Bootstrap failed")
                except Exception as ex:
                    logger.exception("Manual resume: bootstrap_profile failed")
                    st.error(f"{type(ex).__name__}: {ex}")
        st.caption(
            "Reads PDFs from `./resume/`, asks Gemini to merge them into one master profile. "
            "Takes 30–60 seconds. Only needed once, or when you add new resume PDFs."
        )
    else:
        with st.expander("Paste & extract from job posting (recommended)", expanded=True):
            st.caption(
                "Paste an Indeed/LinkedIn/company page below. Gemini pulls out the JD, "
                "title, and company. You can still edit any of them below before tailoring."
            )
            mr_pasted = st.text_area(
                "Paste job posting",
                height=240,
                key="mr_paste_box",
                placeholder="Copy the entire posting from your browser and paste here...",
            )
            if st.button("Extract fields", key="mr_extract_btn", type="primary"):
                if not (mr_pasted or "").strip():
                    st.warning("Paste something first.")
                else:
                    from job_pipeline.job_extractor import extract_job_fields

                    with st.spinner("Extracting fields with Gemini..."):
                        result = extract_job_fields(mr_pasted)
                    if result.get("error"):
                        st.error(f"Extraction failed: {result['error']}")
                    else:
                        st.session_state["mr_jd_box"] = result.get("description", "")
                        st.session_state["mr_job_title_box"] = result.get("title", "")
                        st.session_state["mr_company_box"] = result.get("company", "")
                        st.success("Fields extracted. Review below, then click Tailor resume.")
                        _rerun()

        mr_jd = st.text_area("Job description", height=280, key="mr_jd_box")
        mr_mode = st.radio(
            "Mode",
            ["both", "resume only", "cover letter only"],
            horizontal=True,
            key="mr_mode_box",
            help="both = tailored resume + cover letter; resume only; cover letter only (optional attached resume).",
        )
        mode_map = {
            "both": "both",
            "resume only": "resume_only",
            "cover letter only": "cover_letter_only",
        }
        mr_mode_val = mode_map.get(mr_mode, "both")
        try:
            _assets_mr = assets_fn()
        except Exception:
            _assets_mr = {}
        _cl_templates = [
            t for t in (_assets_mr.get("cover_letter_templates") or []) if isinstance(t, dict)
        ]
        _dr_mr, _dt_mr = get_default_apply_asset_ids()
        mr_template_id = _dt_mr or "template_main"
        if len(_cl_templates) > 1:
            _tpl_labels = {str(t.get("id")): str(t.get("id")) for t in _cl_templates if t.get("id")}
            mr_template_id = st.selectbox(
                "Cover letter template",
                options=list(_tpl_labels.keys()),
                index=0,
                key="mr_template_box",
            )
        mr_attached = ""
        if mr_mode_val == "cover_letter_only":
            mr_attached = st.text_input(
                "Attached resume path (PDF/DOCX, optional)",
                key="mr_attached_resume_box",
                help="Grounds the cover letter in an existing resume file.",
            )
        r1, r2, r3 = st.columns(3)
        mr_title = r1.text_input("Job title", key="mr_job_title_box")
        mr_co = r2.text_input("Company", key="mr_company_box")
        mr_theme = r3.selectbox(
            "RenderCV theme",
            ["classic", "sb2nov", "moderncv", "engineeringresumes"],
            index=3,
            key="mr_theme_box",
        )
        mr_strat = st.selectbox(
            "Tailoring strategy",
            ["conservative", "balanced", "aggressive"],
            index=1,
            key="mr_strat_box",
        )
        st.checkbox(
            "Ask gap-fill questions before rendering",
            value=False,
            key="mr_ask_gap_questions",
            help=(
                "Unchecked (default): one-shot flow — tailor, silently merge grounded gap "
                "suggestions when present, optional second tailor pass, then PDF automatically. "
                "Checked: show gap questions and wait for your answers before PDF."
            ),
        )

        def _mr_clear_gap_widgets() -> None:
            for k in list(st.session_state.keys()):
                if str(k).startswith("mr_gappa_"):
                    del st.session_state[k]

        _jd_len = len((mr_jd or "").strip())
        _jd_ready = _jd_len >= 60
        if not _jd_ready:
            st.caption(f"Paste a job description above (~60 chars min — currently {_jd_len}).")

        if st.button(
            "Build application" if mr_mode_val != "resume_only" else "Tailor resume",
            type="primary",
            key="mr_btn_tailor",
            disabled=not _jd_ready,
        ):
            _mr_clear_gap_widgets()
            st.session_state.pop("mr_auto_pdf_path", None)
            st.session_state.pop("mr_auto_pdf_diag", None)
            st.session_state.pop("mr_built_application", None)
            jd_s = (mr_jd or "").strip()
            if len(jd_s) < 60:
                st.error("Paste a fuller job description (at least ~60 characters).")
            elif mr_mode_val in ("both", "cover_letter_only"):
                try:
                    with st.spinner("Building application package…"):
                        built = build_application_artifacts(
                            mode=mr_mode_val,
                            tailor_resume=(mr_mode_val != "cover_letter_only"),
                            title=mr_title,
                            company=mr_co,
                            location="",
                            description=jd_s,
                            resume_id=_dr_mr or "",
                            template_id=mr_template_id,
                            attached_resume_path=(mr_attached or "").strip() or None,
                            strategy_level=mr_strat,
                            theme=mr_theme,
                            outputs_root=str(repo_root),
                        )
                    st.session_state["mr_built_application"] = built
                    if not built.get("ok"):
                        st.error(built.get("error") or "Build failed")
                    else:
                        tr = built.get("tailored_resume") or {}
                        st.session_state["mr_gaps"] = detect_gaps(
                            jd_s,
                            profile_text=profile_txt,
                            tailored_content=tr.get("content"),
                            use_llm=False,
                        )
                        # Both / cover-letter modes surface explicit per-document
                        # download buttons below (resume PDF + cover letter PDF), so we
                        # do not also set the single auto_pdf button here (avoids a
                        # confusing duplicate "Download PDF").
                        st.session_state["mr_auto_pdf_path"] = ""
                        st.session_state["mr_auto_pdf_diag"] = ""
                except Exception as ex:
                    st.exception(ex)
            else:
                ask_gap = bool(st.session_state.get("mr_ask_gap_questions", False))
                if not ask_gap:
                    try:
                        with st.spinner(
                            "Tailoring resume, merging grounded gap hints, rendering PDF…"
                        ):
                            built = build_application_artifacts(
                                mode="resume_only",
                                tailor_resume=True,
                                title=mr_title,
                                company=mr_co,
                                location="",
                                description=jd_s,
                                strategy_level=mr_strat,
                                theme=mr_theme,
                                outputs_root=str(repo_root),
                            )
                        if not built.get("ok"):
                            st.error(built.get("error") or "Tailoring failed")
                        else:
                            final_mr = built.get("tailored_resume") or {}
                            art = built.get("artifacts") or {}
                            st.session_state["mr_first"] = final_mr
                            st.session_state["mr_final"] = final_mr
                            st.session_state["mr_gaps"] = []
                            st.session_state["mr_gap_prefs"] = []
                            st.session_state["mr_jd_snapshot"] = jd_s
                            st.session_state["mr_auto_pdf_path"] = art.get("resume_pdf") or ""
                            diag = ""
                            for w in art.get("warnings") or []:
                                if w.startswith("Resume PDF render skipped:"):
                                    diag = w.replace("Resume PDF render skipped:", "").strip()
                            st.session_state["mr_auto_pdf_diag"] = diag
                            st.session_state["mr_gaps"] = detect_gaps(
                                jd_s,
                                profile_text=profile_txt,
                                tailored_content=final_mr.get("content"),
                                use_llm=False,
                            )
                    except Exception as ex:
                        st.exception(ex)
                else:
                    try:
                        with st.spinner("Tailoring resume…"):
                            first_try = tailor_resume_from_jd(
                                jd_s,
                                job_title=mr_title,
                                company=mr_co,
                                strategy_level=mr_strat,
                                export_markdown=True,
                            )
                            if not first_try.get("ok"):
                                st.error(
                                    (first_try.get("content") or {}).get("error", "Tailoring failed")
                                )
                            else:
                                gaps_mr = detect_gaps(
                                    jd_s,
                                    profile_text=profile_txt,
                                    tailored_content=first_try.get("content"),
                                )
                                prefs_mr: List[str] = []
                                if pg_mr_ok:
                                    try:
                                        from job_pipeline.db import fetch_gap_answers_for_requirements

                                        reqs_mr = [
                                            (g.get("requirement") or "").strip() for g in gaps_mr
                                        ]
                                        smap_mr = fetch_gap_answers_for_requirements(reqs_mr)
                                        prefs_mr = [smap_mr.get(r, "") for r in reqs_mr]
                                    except Exception as ex:
                                        st.warning(f"Could not load saved gap answers: {ex}")
                                        prefs_mr = [""] * len(gaps_mr)
                                else:
                                    prefs_mr = [""] * len(gaps_mr)

                                st.session_state["mr_first"] = first_try
                                st.session_state["mr_gaps"] = gaps_mr
                                st.session_state["mr_gap_prefs"] = prefs_mr
                                st.session_state["mr_final"] = None
                                st.session_state["mr_jd_snapshot"] = jd_s
                                st.session_state["mr_auto_pdf_path"] = ""
                                st.session_state["mr_auto_pdf_diag"] = ""
                                _rerun()
                    except Exception as ex:
                        st.exception(ex)

        first_mr = st.session_state.get("mr_first")
        built_app = st.session_state.get("mr_built_application")
        if built_app and built_app.get("ok"):
            art = built_app.get("artifacts") or {}
            st.success("Application artifacts ready.")
            bd1, bd2 = st.columns(2)
            with bd1:
                _b_resume = art.get("resume_pdf") or art.get("resume_file")
                if _b_resume and Path(_b_resume).is_file():
                    st.download_button(
                        "⬇ Resume PDF",
                        data=_cached_file_bytes(str(_b_resume)),
                        file_name=Path(_b_resume).name,
                        mime="application/octet-stream",
                        key="mr_built_dl_resume",
                    )
            with bd2:
                _b_cover = art.get("cover_pdf")
                if _b_cover and Path(_b_cover).is_file():
                    st.download_button(
                        "⬇ Cover letter PDF",
                        data=_cached_file_bytes(str(_b_cover)),
                        file_name=Path(_b_cover).name,
                        mime="application/pdf",
                        key="mr_built_dl_cover",
                    )
            for key in ("resume_md", "resume_pdf", "cover_letter_md", "cover_pdf"):
                if art.get(key):
                    st.caption(f"{key}: `{art[key]}`")
            if built_app.get("letter"):
                st.text_area(
                    "Cover letter",
                    value=built_app.get("letter") or "",
                    height=220,
                    key="mr_built_cl_preview",
                )
            for w in art.get("warnings") or []:
                st.warning(w)
        gaps_mr_live: List[Dict[str, Any]] = list(st.session_state.get("mr_gaps") or [])
        prefs_mr_live: List[str] = list(st.session_state.get("mr_gap_prefs") or [])
        ask_gap_ui = bool(st.session_state.get("mr_ask_gap_questions", False))

        if gaps_mr_live:
            with st.expander(f"JD requirement gaps ({len(gaps_mr_live)})", expanded=bool(gaps_mr_live)):
                _render_jd_gaps_panel(gaps_mr_live, key_prefix="mr")

        disp_mr = st.session_state.get("mr_final") or first_mr
        if disp_mr and isinstance(disp_mr.get("validation"), dict):
            val_issues = disp_mr["validation"].get("issues") or []
            hype = [i for i in val_issues if str(i).startswith("Anti-hype")]
            named_miss = [i for i in val_issues if "named requirement not surfaced" in str(i).lower()]
            if hype or named_miss:
                for msg in hype + named_miss:
                    st.warning(msg)
        if disp_mr and disp_mr.get("markdown_path"):
            st.success(f"Markdown draft: `{disp_mr.get('markdown_path')}`")
            md_path = Path(str(disp_mr["markdown_path"]))
            if md_path.is_file():
                try:
                    md_preview = md_path.read_text(encoding="utf-8")
                    with st.expander("Markdown preview", expanded=True):
                        st.markdown(md_preview[:200000])
                except Exception as ex:
                    st.warning(f"Could not read markdown: {ex}")

        auto_pdf = (st.session_state.get("mr_auto_pdf_path") or "").strip()
        auto_pdf_diag = (st.session_state.get("mr_auto_pdf_diag") or "").strip()
        if auto_pdf:
            st.success(f"PDF ready: `{auto_pdf}`")
            try:
                st.download_button(
                    "Download PDF",
                    data=_cached_file_bytes(auto_pdf),
                    file_name=Path(auto_pdf).name,
                    mime="application/pdf",
                    key="mr_dl_pdf_auto",
                )
            except Exception:
                pass
        elif auto_pdf_diag:
            st.warning(f"Automatic PDF render did not produce a file: {auto_pdf_diag}")

        if gaps_mr_live and ask_gap_ui:
            st.markdown("### Gap questions")
            for i, g in enumerate(gaps_mr_live):
                st.markdown(f"**{i + 1}. {g.get('requirement')}** ({g.get('severity')})")
                st.caption(str(g.get("question") or ""))
                suggestion = (g.get("suggested_answer") or "").strip()
                if suggestion:
                    st.caption(f"_Suggested (from career_master):_ {suggestion}")
                wkey = f"mr_gappa_{i}"
                if wkey not in st.session_state:
                    saved = (prefs_mr_live[i] if i < len(prefs_mr_live) else "") or ""
                    st.session_state[wkey] = saved.strip() or suggestion
                st.text_input(f"Answer {i + 1}", key=wkey)

            col_ap, col_pdf = st.columns(2)
            with col_ap:
                if st.button("Apply answers & re-tailor", key="mr_btn_apply"):
                    jd_use = (st.session_state.get("mr_jd_snapshot") or mr_jd or "").strip()
                    ans_mr = [
                        str(st.session_state.get(f"mr_gappa_{i}", "")).strip()
                        for i in range(len(gaps_mr_live))
                    ]
                    extras_mr = answers_to_extra_facts(gaps_mr_live, ans_mr)
                    if pg_mr_ok:
                        try:
                            from job_pipeline.db import persist_gap_answer_rows

                            n_mr = persist_gap_answer_rows(
                                gaps_mr_live,
                                ans_mr,
                                jd_text=jd_use,
                                company_name=mr_co,
                                job_title=mr_title,
                            )
                            if n_mr:
                                st.info(f"Saved {n_mr} answer(s) to the gap_answers library.")
                        except Exception as ex:
                            st.warning(f"Could not save gap answers: {ex}")
                    try:
                        second_try = tailor_resume_from_jd(
                            jd_use,
                            job_title=mr_title,
                            company=mr_co,
                            strategy_level=mr_strat,
                            extra_facts=extras_mr,
                            export_markdown=True,
                        )
                        if second_try.get("ok"):
                            st.session_state["mr_final"] = second_try
                        else:
                            st.warning((second_try.get("content") or {}).get("error", "Second pass failed"))
                    except Exception as ex:
                        st.exception(ex)
                    _rerun()
            with col_pdf:
                if st.button("Render PDF", key="mr_btn_pdf"):
                    jd_use = (st.session_state.get("mr_jd_snapshot") or mr_jd or "").strip()
                    ans_mr = [
                        str(st.session_state.get(f"mr_gappa_{i}", "")).strip()
                        for i in range(len(gaps_mr_live))
                    ]
                    extras_mr = answers_to_extra_facts(gaps_mr_live, ans_mr)
                    final_mr = st.session_state.get("mr_final") or first_mr
                    if gaps_mr_live and extras_mr and not st.session_state.get("mr_final"):
                        try:
                            second_try = tailor_resume_from_jd(
                                jd_use,
                                job_title=mr_title,
                                company=mr_co,
                                strategy_level=mr_strat,
                                extra_facts=extras_mr,
                                export_markdown=True,
                            )
                            if second_try.get("ok"):
                                final_mr = second_try
                                st.session_state["mr_final"] = second_try
                        except Exception as ex:
                            st.warning(str(ex))
                    if pg_mr_ok and gaps_mr_live:
                        try:
                            from job_pipeline.db import persist_gap_answer_rows

                            persist_gap_answer_rows(
                                gaps_mr_live,
                                ans_mr,
                                jd_text=jd_use,
                                company_name=mr_co,
                                job_title=mr_title,
                            )
                        except Exception:
                            pass
                    c_mr = (final_mr.get("content") or {}) if final_mr else {}
                    if not final_mr:
                        st.error("Tailor the resume before rendering PDF.")
                    elif not isinstance(c_mr, dict) or c_mr.get("error"):
                        st.error(f"No tailored content to render: {c_mr.get('error', 'unknown error')}")
                    elif not (c_mr.get("experience") or c_mr.get("summary")):
                        st.error("Tailored content looks empty (no summary or experience).")
                    else:
                        profile_mr = load_consolidated_profile()
                        contact_mr = profile_mr.get("contact") if isinstance(profile_mr.get("contact"), dict) else {}
                        pdf_path_mr, diag_mr = render_tailored_resume_pdf(
                            final_mr.get("content") or {},
                            contact=contact_mr,
                            name=str(profile_mr.get("name") or ""),
                            headline=str(profile_mr.get("headline") or ""),
                            job_title=mr_title,
                            company=mr_co,
                            item_id=(
                                final_mr["item_id"]
                                if isinstance(final_mr.get("item_id"), int)
                                else 0
                            ),
                            military_service=profile_mr.get("military_service") or [],
                            education=profile_mr.get("education") or [],
                            certifications=profile_mr.get("certifications") or [],
                            outputs_root=str(repo_root),
                            theme=mr_theme,
                            strategy_level=mr_strat,
                        )
                        if pdf_path_mr:
                            st.success(f"PDF written to `{pdf_path_mr}`")
                            try:
                                st.download_button(
                                    "Download PDF",
                                    data=_cached_file_bytes(str(pdf_path_mr)),
                                    file_name=Path(pdf_path_mr).name,
                                    mime="application/pdf",
                                    key="mr_dl_pdf",
                                )
                            except Exception:
                                pass
                        else:
                            st.error(f"PDF not rendered: {diag_mr}")
        elif first_mr and not auto_pdf:
            col_pdf2 = st.columns(1)[0]
            with col_pdf2:
                if st.button("Render PDF (no gaps)", key="mr_btn_pdf_nogaps"):
                    profile_mr = load_consolidated_profile()
                    contact_mr = profile_mr.get("contact") if isinstance(profile_mr.get("contact"), dict) else {}
                    pdf_path_mr, diag_mr = render_tailored_resume_pdf(
                        (first_mr.get("content") or {}),
                        contact=contact_mr,
                        name=str(profile_mr.get("name") or ""),
                        headline=str(profile_mr.get("headline") or ""),
                        job_title=mr_title,
                        company=mr_co,
                        item_id=(
                            first_mr["item_id"]
                            if isinstance(first_mr.get("item_id"), int)
                            else 0
                        ),
                        military_service=profile_mr.get("military_service") or [],
                        education=profile_mr.get("education") or [],
                        certifications=profile_mr.get("certifications") or [],
                        outputs_root=str(repo_root),
                        theme=mr_theme,
                        strategy_level=mr_strat,
                    )
                    if pdf_path_mr:
                        st.success(f"PDF written to `{pdf_path_mr}`")
                    else:
                        st.error(f"PDF not rendered: {diag_mr}")
