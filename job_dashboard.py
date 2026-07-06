"""Jobs Aggregator — Streamlit Dashboard.

Read-only view of ingested job postings: stats, source breakdown,
fit scoring, keyword overlap, and LLM summaries.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass

import streamlit as st

from job_pipeline.db import (
    count_items_by_status,
    count_closed_by_reason,
    get_item,
    init_job_pipeline_schema,
    list_queue,
    list_queue_source_counts,
    category_counts,
)
from job_pipeline.lane_category import CATEGORY_ORDER, category_label

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Jobs Aggregator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── helpers ──────────────────────────────────────────────────────────────

def _parse_summary(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _fit_score_from_row(row: Dict[str, Any], summary: Dict[str, Any]) -> Optional[float]:
    score = row.get("fit_score")
    if score is not None:
        return float(score)
    for key in ("fit_score_blended", "fit_score_raw", "fit_score_0_1"):
        val = summary.get(key)
        if val is not None:
            return float(val)
    return None


def _fit_color(score: float) -> str:
    if score >= 0.7:
        return "🟢"
    if score >= 0.4:
        return "🟡"
    return "🔴"


def _verdict_badge(verdict: str, fit_score: Optional[float] = None) -> str:
    v = (verdict or "").lower()
    if v == "strong_match" or (v == "maybe" and fit_score is not None and fit_score >= 0.7):
        return "✅ Strong Match"
    if v == "maybe":
        if fit_score is not None and fit_score >= 0.4:
            return "🟡 Possible Fit"
        return "🟡 Maybe"
    return "❌ Pass"


# ── sidebar ──────────────────────────────────────────────────────────────

def _sidebar():
    st.sidebar.title("Jobs Aggregator")
    st.sidebar.markdown("Multi-source job ingest, dedup, LLM scoring & keyword fit.")

    if st.sidebar.button("🔄 Run Ingest"):
        with st.spinner("Ingesting from all sources..."):
            from job_pipeline.service import svc_ingest
            result = svc_ingest()
            st.sidebar.success(f"Ingest done: {json.dumps(result.get('ingest', result), indent=2)[:500]}")

    if st.sidebar.button("📝 Summarize New"):
        with st.spinner("Summarizing ingested jobs..."):
            from job_pipeline.service import svc_summarize
            result = svc_summarize(limit=25)
            count = result.get("summarized", 0)
            st.sidebar.success(f"Summarized {count} jobs")

    st.sidebar.markdown("---")

    rescore_limit = st.sidebar.number_input("Re-score limit", min_value=1, max_value=500, value=25)

    status_filter = st.sidebar.selectbox(
        "Status filter",
        ["pending_review", "ingested", "closed", "all"],
        index=0,
    )

    category_filter = st.sidebar.selectbox(
        "Category",
        ["all"] + list(CATEGORY_ORDER),
        index=0,
        format_func=lambda c: category_label(c) if c != "all" else "All Categories",
    )

    if st.sidebar.button("🔁 Re-score Jobs"):
        cat = None if category_filter == "all" else category_filter
        progress = st.sidebar.progress(0, text="Re-scoring...")
        from job_pipeline.service import svc_resummarize

        def _on_progress(i, total, done, failed):
            progress.progress(i / total, text=f"Re-scoring {i}/{total} ({done} done, {failed} failed)")

        result = svc_resummarize(limit=int(rescore_limit), category=cat, on_progress=_on_progress)
        progress.empty()
        st.sidebar.success(f"Re-scored {result['resummarized']} jobs ({result['failed']} failed)")
        if result.get("errors"):
            st.sidebar.warning("\n".join(result["errors"][:5]))

    source_filter = st.sidebar.selectbox(
        "Source",
        ["all", "indeed", "usajobs", "jobspy", "hn_whoishiring", "feeds", "manual"],
        index=0,
    )

    limit = st.sidebar.slider("Max results", 10, 200, 50)

    return {
        "status": None if status_filter == "all" else status_filter,
        "category": None if category_filter == "all" else category_filter,
        "source": None if source_filter == "all" else source_filter,
        "limit": limit,
    }


# ── stats bar ────────────────────────────────────────────────────────────

def _stats_bar():
    ingested = count_items_by_status("ingested")
    pending = count_items_by_status("pending_review")
    closed = count_items_by_status("closed")
    total = count_items_by_status()

    cols = st.columns(4)
    cols[0].metric("Total Jobs", total)
    cols[1].metric("Ingested (unsummarized)", ingested)
    cols[2].metric("Pending Review", pending)
    cols[3].metric("Closed / Filtered", closed)


# ── source breakdown ─────────────────────────────────────────────────────

def _source_breakdown():
    st.subheader("Source Breakdown")
    try:
        sources = list_queue_source_counts("pending_review")
        if sources:
            cols = st.columns(min(len(sources), 6))
            for i, src in enumerate(sources):
                cols[i % len(cols)].metric(
                    src.get("source", "?").upper(),
                    src.get("count", 0),
                )
        else:
            st.info("No pending review items yet. Run Ingest + Summarize.")
    except Exception as e:
        st.error(f"Source counts failed: {e}")


# ── category breakdown ───────────────────────────────────────────────────

def _category_breakdown():
    st.subheader("Category Breakdown")
    try:
        cats = category_counts("pending_review")
        if cats:
            cols = st.columns(min(len(cats), 5))
            for i, (cat, count) in enumerate(sorted(cats.items(), key=lambda x: -x[1])):
                cols[i % len(cols)].metric(category_label(cat), count)
        else:
            st.info("No categorized items yet.")
    except Exception as e:
        st.error(f"Category counts failed: {e}")


# ── job card ─────────────────────────────────────────────────────────────

def _render_job_card(row: Dict[str, Any]):
    summary = _parse_summary(row.get("summary_json"))
    title = row.get("title") or summary.get("role") or "Untitled"
    company = row.get("company_name") or summary.get("company") or "Unknown"
    location = row.get("location") or summary.get("location") or ""
    source = (row.get("source") or "").upper()
    fit_score = _fit_score_from_row(row, summary)
    verdict = summary.get("verdict", "")
    key_reqs = summary.get("key_requirements") or []
    why_match = summary.get("why_match") or summary.get("headline") or ""
    salary = row.get("salary_text") or summary.get("salary") or ""
    job_url = row.get("job_url") or row.get("apply_url") or ""
    list_rank = row.get("list_rank")

    with st.container():
        header_cols = st.columns([4, 1, 1])
        with header_cols[0]:
            link = f"[{title}]({job_url})" if job_url else title
            st.markdown(f"### {link}")
            st.caption(f"{company} · {location} · {source}")
        with header_cols[1]:
            if fit_score is not None:
                st.metric("Fit Score", f"{_fit_color(fit_score)} {fit_score:.0%}")
            if list_rank is not None:
                st.caption(f"Rank: {list_rank:.2f}")
        with header_cols[2]:
            st.markdown(f"**{_verdict_badge(verdict, fit_score)}**")
            if salary:
                st.caption(f"💰 {salary}")

        # Details
        if why_match:
            st.markdown(f"_{why_match}_")

        if key_reqs:
            req_tags = " · ".join(f"`{r}`" for r in key_reqs[:8])
            st.markdown(f"**Key Requirements:** {req_tags}")

        # ATS keyword overlap (if present in summary)
        ats_overlap = summary.get("ats_overlap") or summary.get("keyword_overlap")
        if isinstance(ats_overlap, dict):
            matched = ats_overlap.get("matched") or []
            missing = ats_overlap.get("missing") or []
            if matched or missing:
                overlap_cols = st.columns(2)
                with overlap_cols[0]:
                    if matched:
                        st.markdown("**Matched Keywords:** " + ", ".join(f"✅ {k}" for k in matched[:10]))
                with overlap_cols[1]:
                    if missing:
                        st.markdown("**Missing Keywords:** " + ", ".join(f"❌ {k}" for k in missing[:10]))

        st.markdown("---")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    init_job_pipeline_schema()
    filters = _sidebar()

    st.title("📊 Jobs Aggregator Dashboard")
    st.caption("Multi-source job ingestion · Deduplication · LLM fit scoring · Keyword overlap analysis")

    _stats_bar()

    tab_queue, tab_sources, tab_categories = st.tabs(
        ["Job Queue", "Sources", "Categories"]
    )

    with tab_queue:
        rows = list_queue(
            status=filters["status"],
            limit=filters["limit"],
            order_by_rank=True,
            source=filters["source"],
            category=filters["category"],
        )
        if not rows:
            st.info("No jobs match the current filters. Try running Ingest + Summarize.")
        else:
            st.caption(f"Showing {len(rows)} jobs")
            for row in rows:
                _render_job_card(row)

    with tab_sources:
        _source_breakdown()

    with tab_categories:
        _category_breakdown()


if __name__ == "__main__":
    main()
