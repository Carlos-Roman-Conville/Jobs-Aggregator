# AI agent brief — AI job application pipeline

You are picking up an in-progress personal job-application automation tool. This brief is everything you need to be productive without reading the whole repo. Read this first, then [README.md](README.md) and [SYSTEM_DESIGN_AND_ROADMAP.md](SYSTEM_DESIGN_AND_ROADMAP.md) when you need detail.

---

## TL;DR

Postgres-backed pipeline that ingests jobs from many boards → LLM-triages each posting → scores against the user's career profile (operations-aware, location-aware, seniority-aware, ATS-aware) → routes to a Streamlit review queue → builds resume + cover letter packages → tracks outcomes. Owner uses it for his own job search. Stack: Python 3.10, PostgreSQL, FastAPI ([api_server.py](api_server.py)), Streamlit ([job_dashboard.py](job_dashboard.py)), OpenAI (triage), Gemini (resume tailoring), rendercv (PDF), optional browser-use (auto-apply).

### LLM wiring (two providers)

| Provider | Role | Configuration |
|----------|------|----------------|
| **OpenAI** | Job summarize / queue scoring (`summarize.py`) | `OPENAI_API_KEY`, `OPENAI_JOB_SUMMARY_MODEL`, retry env vars |
| **Google Gemini** | Consolidated resume bootstrap, tailoring, gap-fill LLM pass, career helper, cover letters, package check | `GEMINI_API_KEY` or `GOOGLE_API_KEY`; per-task model env vars resolved in [`job_pipeline/genai_settings.py`](job_pipeline/genai_settings.py). Optional **`GEMINI_MODEL`** sets one fallback ID when a role-specific variable is unset (useful while comparing models). |

---

## Owner profile (use this for every scoring/tailoring decision)

**Carlos Roman-Conville** — Philadelphia, PA. Currently Technical Operations Manager at BEAT THE BOMB (Linux Photon servers, NUCs, networked CCTV/RFID/DMX/Dante audio/OBS, RustDesk remote admin, hardware swap, SOPs). Prior: Operations Manager at 1-800-GOT-JUNK; 8-year US Army Reserve combat medic veteran (68W, 338th Medical Brigade). BA Political Science, Rowan, 3.80 GPA.

**Self-positioning for job search:**
- **Frame as Jr Sysadmin (~2 years technical experience)** for tech-heavy IC roles
- Regular/manager-level OK for non-tech-heavy roles where he's clearly qualified
- Currently learning systems via AI tools (Cursor, n8n) — extending into the software side
- Honest skill gaps for "Regular Sysadmin" postings: AD/Group Policy, fleet patching (WSUS/SCCM/Intune), hypervisor admin, backup architecture, enterprise IAM (Okta/Azure AD), real cloud depth, ServiceNow/Jira SD admin

**Target roles, ranked:**
1. Aligned: Tier 2 IT support, Jr Sysadmin, NOC technician, Tech Ops, IT operations analyst, helpdesk lead, field service, technical support engineer
2. Stretch: Regular Sysadmin (3-5 yr postings) — interview-able if Linux/networking-heavy
3. **Reject**: Senior/Staff/Principal IC engineering, DevOps, SRE, Cloud Architect (unless title literally says Junior/Associate/Entry); pure operations / warehousing / fulfillment / customer service

**Location, ranked (encoded in `filters.location_policy`):**
1. **Remote** (national or US) — primary
2. **Hybrid Philadelphia** — acceptable
3. **Onsite Philadelphia** — last resort, AND must be tech-related
4. **Onsite outside Philly metro** — reject

---

## Hard rules (do not violate)

1. **Resume PDF = RenderCV.** Use **rendercv** ([job_pipeline/rendercv_export.py](job_pipeline/rendercv_export.py)) for ATS-friendly PDF generation from tailored YAML/Markdown — no third-party design APIs.
2. **No fictional resume bullets.** Resume tailor must only use facts grounded in the user's existing profile (`career_understanding.py` + reference PDFs). If the JD asks for something the profile doesn't cover, surface a gap question — don't invent.
3. **`career_profile.json` is the source of truth** for what the pipeline scores as a good fit. It currently encodes Jr-IT/remote-first/vet framing. Do not regress this back to the old "operations-first, tech-as-support" framing.
4. **Constraints in profile:** `claim_years_technical_experience: 2`, `max_apply_min_years_experience_gap: 2`. The summarize.py YOE cap uses these — postings asking for >4 years experience get model-fit capped at 0.45.
5. **License compatibility:** This repo is MIT. Do not paste code from AGPL projects (e.g. `Pickle-Pixel/ApplyPilot`). Inspiration only, then rewrite. See [GITHUB_REPOS_TO_BORROW.md](GITHUB_REPOS_TO_BORROW.md) for the OSS reference list.
6. **Goal: max source coverage.** Adding noisy sources is low-cost (downstream filters handle relevance). Adding a missed-but-relevant source is high-value. Default to "let's add another source" over "let's polish existing ones."

---

## Architecture map

```
ingest.py                       ingest sources → upsert_posting → DB
  ├── Greenhouse/Lever (built-in)
  ├── Apify Indeed (paid, optional)
  └── job_pipeline/sources/
       ├── jobspy_source.py      LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter
       ├── usajobs_source.py     federal API, vet-friendly
       ├── feeds_source.py       RemoteOK, Arbeitnow, Remotive, The Muse, Jobicy,
       │                          Working Nomads, WeWorkRemotely RSS
       └── hn_whoishiring.py     HN "Who is hiring?" monthly Algolia thread

summarize.py                    LLM triage → multi-stage scoring
  pipeline:
    1. OpenAI gpt-4.1-mini → JSON {verdict, fit_score, gaps, seniority_fit, ...}
    2. heuristic skill-hit ratio
    3. ats_score.py: jaccard + keyword overlap vs canonical resume blob
    4. Base blended = 0.52*model + 0.33*heuristic + 0.15*ATS
    5. YOE cap (deterministic, profile.constraints driven)
    6. Seniority multiplier (title parse + LLM seniority_fit label)
    7. domain_fit.py multiplier (target/avoid families)
    8. location_policy.py multiplier or hard-reject
    9. auto-close decision

service.py                      orchestration: ensure_schema, ingest, summarize,
                                queue, decide, build_package, record_outcome,
                                imap_rejection_sync, browser_apply_preview

job_dashboard.py                Streamlit UI: queue review with score-chain
                                breakdown, status board grouped by filter reason,
                                package-ready review, analytics, manual add

job_pipeline/
  ├── db.py                     Postgres state machine + upserts
  ├── states.py                 ingested → pending_review → drafted → approved
  │                              → package_ready → submitted → outcome
  ├── inbox_sync.py             IMAP rejection-email sync (closes submitted →
  │                              rejected when employers email)
  ├── auto_apply/browser_agent.py  guarded browser-use wrapper (gate on approval +
  │                              daily budget; never bulk-submit)
  └── rendercv_export.py        YAML → ATS PDF via rendercv CLI (env-gated)
```

---

## Recent state (May 2026)

**Ships now:**
- Career profile rewritten for Jr IT/remote-first/vet ([job_pipeline/career_profile.json](job_pipeline/career_profile.json))
- Location policy with metro-keyword + tech-only-onsite logic
- Seniority multiplier with title-token parsing + YOE deterministic caps
- ATS overlap scoring (Jaccard + keyword harvest, no vendor APIs)
- 6+ ingest sources beyond the original Greenhouse/Lever/Apify-Indeed:
  JobSpy aggregator, USAJobs, RemoteOK, Arbeitnow, Remotive, The Muse, Jobicy,
  Working Nomads, WeWorkRemotely RSS, HackerNews "Who is hiring?"
- Dashboard surfaces full multiplier chain + filter reasons (so you can debug
  why anything was scored / closed)
- IMAP rejection sync scaffolded
- browser-use wrapper scaffolded with daily-budget gate
- rendercv hook in package build (env-gated `JOB_PIPELINE_RENDERCV_RENDER=1`)

**All new sources default to `enabled: false` in [job_pipeline_config.json](job_pipeline_config.json).** Owner enables them as he wants more volume.

---

## Next priorities (in order)

1. **Resume PDF parser bootstrap.** Owner has 10 resume variants in `/resume/*.pdf`. Build a one-shot script that reads them all (pdfplumber + LLM), extracts canonical phrasing/skills/dates, and merges findings into [job_pipeline/career_profile.json](job_pipeline/career_profile.json) and [application_assets.json](application_assets.json). Highest leverage — improves every downstream call. Pure new file, no risk.

2. **Resume gap-question Streamlit flow.** When the resume tailor detects a JD requirement that's not in the profile, push it to a `gap_questions` DB table; surface in a new dashboard tab with Y/N + 1-line answer; re-run tailor with augmented profile. Touches: [job_pipeline/resume_tailor.py](job_pipeline/resume_tailor.py), [job_dashboard.py](job_dashboard.py), [job_pipeline/schema.sql](job_pipeline/schema.sql).

3. **Per-company ATS adapters** following the Greenhouse/Lever pattern in [job_pipeline/ingest.py](job_pipeline/ingest.py): **Ashby** (`api.ashbyhq.com/posting-api/job-board/{org}`), **Workable** (`apply.workable.com/api/v1/widget/accounts/{id}`), **SmartRecruiters**, **Recruitee**. Each is ~60-100 lines + a config list of company slugs.

4. **Tests for the scoring stack.** No tests exist yet. The domain × seniority × YOE × location × ATS multiplier chain has lots of room for math drift. Start with [job_pipeline/summarize.py](job_pipeline/summarize.py) `_seniority_multiplier`, `_apply_yoe_cap`, then `evaluate_location_policy`, then a smoke test for the full `summarize_pipeline_item` against a fixture.

5. **Workday tenant scrapers** for big Philly-area employers (Comcast, Vanguard, Independence Blue Cross, SAP). Workday tenants expose JSON search at `*.myworkdayjobs.com/.../External/jobs/search`. Pattern matches Greenhouse/Lever (per-tenant config list).

6. **Round-3 sources** (lower priority): Built In Philly, Otta, Himalayas (verify API first), VetJobs page scrape, ClearanceJobs (paid or browser-use only).

---

## Tactical notes for the next agent

- **Working directory:** `E:\AI Programs\AI-job-application-pipeline`
- **Run things via:** `launch_all.cmd` (Windows) or `launch_all.ps1` — starts Postgres container, FastAPI, Streamlit
- **Owner uses system Python**, no venv. `requirements.txt` is the dep list.
- **DB:** Postgres in Docker (defaults set in `.env.example`). `python -c "from job_pipeline.service import ensure_schema; print(ensure_schema())"` to init schema.
- **Don't reinvent existing patterns.** New ingest sources must follow `(session, ingest_settings, dedupe_by_normalized_url, stats, errors, ...)` and call `upsert_posting(...)`. New scoring multipliers should append to `summary_json` so the dashboard surfaces them automatically.
- **The dashboard's "score chain" rendering** ([job_dashboard.py](job_dashboard.py) `_render_score_chain_md`) reads specific keys from `summary_json` — if you add a new multiplier, add a corresponding dashboard chip + chain entry.
- **Don't commit `.env`** — it has API keys.
- **Memory of past sessions** lives in `.claude/projects/E--AI-Programs-AI-job-application-pipeline/memory/` — those notes summarize this same context; safe to ignore unless you're using Claude Code with that memory backend.

---

## Follow-up TODOs

### Cover-letter template body still operations-flavored (after IT-first asset metadata refresh)

`application_assets.json` has been re-framed so the dashboard / job-card LLM prompt treats Carlos as IT / Desktop Support primary, with operations as supporting evidence. Only metadata + `maps_to_*` fields reach that prompt, so the existing `cover_letter_templates[0].text` body was intentionally left alone.

That body is still operations-flavored, however, so any cover letter generated for an IT / Desktop Support / Help Desk posting will read against the new card framing. The clean fix (separate commit, out of scope for the metadata refresh):

- Add a second entry to `cover_letter_templates` with an IT / Desktop Support flavored `text` body.
- Give it `maps_to_job_families: ["it_support", "helpdesk"]` (or whatever families [job_pipeline/domain_fit.py](job_pipeline/domain_fit.py) emits for Tier 1/2 IT roles) and `maps_to_title_keywords_any` covering `desktop support`, `it support`, `help desk`, `helpdesk`, `noc`, `systems administrator`, `field service`.
- Optionally add a `suggest_when` block so [application_asset_strategy.py](application_asset_strategy.py) `maybe_override_llm_assets` picks it for IT postings while the existing operations-flavored template stays as the fallback for genuinely operations-flavored postings.

Leave the existing `template_main` entry in place — don't rewrite its body — so non-technical operations postings still get appropriate copy.

---

## Files to read in order if you have time

1. This file
2. [README.md](README.md) — user-facing how-to
3. [SYSTEM_DESIGN_AND_ROADMAP.md](SYSTEM_DESIGN_AND_ROADMAP.md) — phased rollout map, scoring formula, state machine
4. [job_pipeline/career_profile.json](job_pipeline/career_profile.json) — what counts as a "good fit"
5. [job_pipeline/ingest.py](job_pipeline/ingest.py) — source dispatcher (add new sources here)
6. [job_pipeline/summarize.py](job_pipeline/summarize.py) — the scoring stack
7. [job_pipeline/sources/](job_pipeline/sources/) — copy these as templates for new sources
8. [job_dashboard.py](job_dashboard.py) — UI; surface new fields here when you add scoring signals
9. [GITHUB_REPOS_TO_BORROW.md](GITHUB_REPOS_TO_BORROW.md) — OSS reference list with license notes

## Checkpoint TODOs

- **~2 weeks (cover letter templates):** Add an IT-flavored sibling cover-letter template in `application_assets.json` (`cover_letter_templates`) and enable the conditional template dropdown in the Manual application tab (currently hidden when only `template_main` exists).

When in doubt: ask the owner before introducing a new dependency, changing the career profile shape, or removing existing scoring stages.
