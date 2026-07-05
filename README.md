# AI job application pipeline

A personal **job discovery → scoring → review → package → tracking** pipeline. Pulls postings from configurable sources, stores them in PostgreSQL, ranks each opportunity with domain-aware rules plus an LLM summary, optionally builds tailored application artifacts (cover letter + resume PDF paths), and keeps funnel status through submission and outcomes.

This repository is intentionally pragmatic: optimized for shipping a repeatable daily workflow (CLI, FastAPI hooks, Streamlit dashboard, optional n8n digest) rather than for multi-tenant product polish.

---

## End-to-end flow

1. **Ingest** — Scrapers/API clients normalize postings and dedupe by URL (`job_pipeline/ingest.py`).
2. **Normalize** — Canonical URLs and text cleanup (`job_pipeline/normalize.py`).
3. **Summarize / score** — Fit blend (model + heuristic + ATS overlap), domain multiplier from your career profile, seniority caps, salary gate, optional location policy, deterministic **search preferences** (`job_pipeline/search_preferences.md`) (`job_pipeline/summarize.py`, `job_pipeline/domain_fit.py`, `job_pipeline/location_policy.py`, `job_pipeline/search_preferences.py`, `job_pipeline/ats_score.py`).
4. **Queue** — Rows land in **`pending_review`** unless auto-filtered to **`closed`** (`job_pipeline/states.py`).
5. **Decide & package** — Human approves → cover letter (+ optional RenderCV PDF) bundled into **`package_ready`** (`job_pipeline/service.py`, `job_pipeline/package_build.py`).
6. **Submit & outcomes** — Track **`submitted`** → **`rejected`/`responded`**, optionally sync rejection emails via IMAP (`job_pipeline/inbox_sync.py`).
7. **Export** — Spreadsheet tracker (`python -m job_pipeline.export_to_tracker`).

---

## Principal components

| Layer | Role |
|--------|------|
| `job_pipeline/ingest.py` | Orchestrates Greenhouse, Lever, Indeed (Apify), JobSpy (optional), USAJobs, RemoteOK-style feeds → `upsert_posting`. |
| `job_pipeline/domain_fit.py` | Role-family classification vs `job_pipeline/career_profile.json`. |
| `job_pipeline/summarize.py` | OpenAI JSON summary, blended score, auto-close rules. |
| `job_pipeline/service.py` | High-level `svc_*` operations for API and scripts. |
| `job_pipeline/db.py` | PostgreSQL access; runs `schema.sql` + idempotent migrations. |
| `job_pipeline/genai_settings.py` | Gemini API key + model IDs (`GEMINI_*`, `GOOGLE_API_KEY`, optional `GEMINI_MODEL`). |
| `job_dashboard.py` | Streamlit review UI. |
| `api_server.py` | FastAPI façade over `svc_*`. |
| `GITHUB_REPOS_TO_BORROW.md` | Curated OSS references (licenses, integration ideas). |
| **`SYSTEM_DESIGN_AND_ROADMAP.md`** | **Deep dive**: scoring math, config schema, source taxonomy, rollout phases, risks. |

Always start with **this README** for orientation; use **`SYSTEM_DESIGN_AND_ROADMAP.md`** when changing scoring, adding sources, or debugging fit/location behavior.

---

## Stack

- Python 3.10+
- PostgreSQL
- OpenAI (primary job triage / summary)
- Optional: Google Gemini (bootstrap profile, tailoring, gaps LLM, career helper, cover letters, package checks — model IDs in [`job_pipeline/genai_settings.py`](job_pipeline/genai_settings.py))
- Optional: Apify (Indeed), JobSpy (multi-board), USAJobs API, public job JSON/RSS feeds
- Optional: **RenderCV** (YAML → ATS-friendly PDF), **browser-use** (gated auto-fill)
- Streamlit, FastAPI, n8n workflow JSON (daily digest)

---

## Quick setup

1. Clone the repo and create a virtual environment.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set DB + API keys (see comments in `.env.example`).
4. Copy `job_pipeline/career_profile.example.json` to `job_pipeline/career_profile.json` and personalize (the real file is `.gitignore`d by default).
5. Configure `job_pipeline_config.json` (sources, `filters.location_policy`, matching thresholds).
6. Initialize schema:  
   `python -c "from job_pipeline.service import ensure_schema; print(ensure_schema())"`

---

## Typical usage

```powershell
python -c "from job_pipeline.service import svc_ingest, svc_summarize, svc_queue; print(svc_ingest()); print(svc_summarize(25)); print(svc_queue(status='pending_review', limit=10, with_card=True))"
```

- **FastAPI**: `uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload`
- **Streamlit**: `streamlit run job_dashboard.py`
- **Windows launcher**: `launch_all.cmd` or `powershell -ExecutionPolicy Bypass -File .\launch_all.ps1`

Scheduled runs: wire n8n to `POST /jobs/pipeline/daily-run` (optional `X-API-Key` if `N8N_API_KEY` is set).

---

## Manual resume from a pasted JD

For one-off roles (no ingest row yet): consolidate PDFs under `/resume/` into `job_pipeline/consolidated_profile.{md,json}`, then run `make_resume.py` or use the Streamlit **Manual resume** tab. Full workflow, RenderCV setup, gap-fill behavior, and verification commands are documented in **`RESUME_CREATOR_HOWTO.md`**.

---

## Search preferences (`search_preferences.md`)

**Purpose:** Hand-edited authority for **what jobs get prioritized vs auto-closed during ingest scoring**. This file mirrors the precedence pattern of `career_master.md`, but **does not** govern resume tailoring.

**Separation of concerns:**

| Authority | Governs |
|-----------|---------|
| `job_pipeline/search_preferences.md` | Search breadth / ingest seeds (optional), ranking multiplier, deterministic auto-close (`search_preferences:*`) |
| `job_pipeline/career_master.md` + consolidated profile | Resume tailoring honesty limits and framing |

Do **not** wire `search_preferences.md` into tailoring modules (`resume_tailor.py`, etc.).

**Editing:** Change lists and prose directly in `search_preferences.md`; `job_pipeline/search_preferences.py` only parses + scores.

**Hard-close reasons** (stored as `filter_reason=search_preferences:<code>`):

- `title_avoided`
- `salary_below_floor`
- `outside_metro`
- `noise_filtered`

**Config toggles** (`job_pipeline_config.json` → `filters.search_preferences`):

- `enabled` — skip the entire preferences stage when false (`pref_multiplier` pinned to `1.0`, no prefs closes).
- `honor_auto_close` — compute boosts multipliers but ignore hard-close signals when false.
- `apply_multiplier` — honor closes but skip multiplier application when false.

**Re-score existing rows:** `python -m job_pipeline.rescore_preferences` (optional `--dry-run`, `--id <n>`).

**Tests:** `python -m unittest tests.test_search_preferences -v`

---

## Status & license

Personal automation project — functional but evolving.

MIT
