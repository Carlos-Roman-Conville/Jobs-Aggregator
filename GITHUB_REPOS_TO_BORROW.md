# GitHub repos to borrow from — feature-complete shortlist

Audit of the best open-source repos to lift code, patterns, and ideas from to bring your `AI-job-application-pipeline` to feature parity with the leading tools. Verified May 15, 2026.

Your pipeline today: Greenhouse/Lever/Apify-Indeed ingest → OpenAI summarize + score → Canva resume tailor → cover letter → Postgres state machine → FastAPI + Streamlit dashboard → optional Playwright LinkedIn Easy Apply.

Below: each gap, the repos worth raiding, the integration point in your existing code, and the license/maintenance status.

---

## Tier 1 — pull these in first

These are the high-leverage repos. Each one closes a major gap, has a permissive license, and is actively maintained.

### 1. speedyapply/JobSpy — multi-source ingestion in one package

- Repo: https://github.com/speedyapply/JobSpy
- Stars: 3.4k · License: **MIT** · Language: Python · Last release Mar 2025 (still the canonical multi-source scraper)
- What it gives you: LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter, Bayt, Naukri, BDJobs — all behind one `scrape_jobs()` call with a unified schema. Proxy support, salary parsing, full descriptions, remote filter, hours_old filter.

**Where it plugs in:** new module `job_pipeline/sources/jobspy_source.py`, called from `ingest.py` alongside your existing Greenhouse/Lever/Apify-Indeed sources. The `JobPost` schema (title, company, job_url, location, description, min_amount/max_amount, date_posted) maps almost 1:1 onto your `normalize.py` output — should be a half-day integration.

**What to lift:** the whole library as a pip dep (`pip install -U python-jobspy`). You're not vendoring code; you're adopting it as a peer of Apify-Indeed and you can probably retire Apify-Indeed once you confirm JobSpy's Indeed scraper works for your queries (it has no rate limiting per their docs).

---

### 2. srbhr/Resume-Matcher — ATS scoring + JD keyword matching

- Repo: https://github.com/srbhr/Resume-Matcher
- Stars: 26.2k · License: **Apache 2.0** · Language: Python/TypeScript · Latest release Feb 2026 (very active)
- What it gives you: master-resume → JD comparison, keyword highlighting, match score, AI-suggested rewrites. Backend is FastAPI + LiteLLM (supports OpenAI, Anthropic, Gemini, Ollama, OpenRouter, DeepSeek), PDF export via headless Chromium.

**Where it plugs in:** lift their scoring + keyword-extraction code into a new `job_pipeline/ats_score.py` and call it from `domain_fit.py` / `summarize.py`. Today your fit scoring is "does this match my career profile?" — this adds the missing layer of "does my actual resume text match this JD's keywords?" which is what real ATS filters check.

**What to lift:** the backend scoring service in `apps/backend/` — specifically the keyword extraction, embedding-based similarity, and the master-resume → tailored-resume rewrite prompts. Their PDF export pipeline (Playwright + HTML templates) is also a credible alternative to your Canva dependency if Canva auth ever breaks.

---

### 3. browser-use/browser-use — autonomous form filling beyond LinkedIn

- Repo: https://github.com/browser-use/browser-use
- Stars: 83.5k · License: **MIT** · Language: Python · Releases through Mar 2026, used by 2.4k other repos
- What it gives you: the agent layer your current Playwright LinkedIn bot is missing. Give it a task like *"Fill in this Workday application with my resume and information"* and it navigates the form, fills fields, uploads docs. Works with OpenAI, Anthropic, Gemini, or local models via Ollama. Their README literally shows a job-application demo.

**Where it plugs in:** new module `job_pipeline/auto_apply/browser_agent.py`. Triggered from `service.py` when an item enters `package_ready` state. Pass it the job URL, your `application_assets.json`, and the tailored resume from `resume_export.py`. Have it stop before submit and write screenshots into a review queue (consistent with your existing "stops before submit" pattern). This is the single biggest capability jump in this report — it unlocks Workday/Greenhouse/Lever/Ashby auto-fill, not just LinkedIn Easy Apply.

**What to lift:** the library as a pip dep (`uv add browser-use`). Wire your `application_assets.json` and tailored resumes into the `Agent` task prompt. Their `examples/use-cases/apply_to_job.py` is your starter.

---

### 4. rendercv/rendercv — Python-native LaTeX-quality PDF generator

- Repo: https://github.com/rendercv/rendercv
- Stars: 16.6k · License: **MIT** · Language: Python
- What it gives you: YAML resume in → PDF/LaTeX/Markdown/HTML/PNG out. Pure-Python, four built-in themes, custom themes supported. No Canva API, no OAuth refresh.

**Where it plugs in:** alternative implementation of `resume_export.py` / parallel to `canva_resume_template.py`. Keep Canva as the "design-y" path; RenderCV becomes the "reliable, no-auth, fast" path. Your `resume_tailor.py` already produces the structured resume; just write a YAML serializer and call RenderCV's CLI.

**What to lift:** library as a pip dep. Have your tailor module dual-write: a tailored Canva render *and* a tailored RenderCV PDF. Use the RenderCV PDF as a fallback when Canva fails, and possibly as the "ATS-friendly plain version" to submit alongside the designed one.

---

## Tier 2 — borrow patterns, not code

These have ideas worth stealing but the licenses or stacks make wholesale code-lifting risky. Read them, copy the architecture.

### 5. jakemercure28/job-search-automation — 11 ATS platforms + IMAP rejection sync

- Repo: https://github.com/jakemercure28/job-search-automation
- Stars: 2 · License: **MIT** · Language: Node.js (so: borrow patterns, not code) · 163 commits, recently active
- Why it matters: scrapes Greenhouse, Lever, Ashby, Workable, Workday, Wellfound, Built In, Rippling, RemoteOK, Jobicy, Arbeitnow, WeWorkRemotely. Has Gemini-based LLM scoring with deterministic post-processing caps (e.g. "8+ YOE roles always cap at 3 regardless of LLM output"), a complexity classifier that tags jobs `simple` vs `complex` so you know whether to auto-apply or hand-review, an **IMAP rejection-email sync** that watches Gmail and auto-transitions your DB items to `rejected`, and a voice-checker (`lib/voice-check.js`) that flags AI-flavored buzzwords/em-dashes in generated answers.

**What to borrow into your codebase:**
- **Ashby/Workable/Wellfound/RemoteOK/Built In/Jobicy/Arbeitnow/WeWorkRemotely scrapers.** Re-implement these in Python under `job_pipeline/sources/`. Each is a thin API or HTML scraper — none are hard once you've seen the endpoints. His `scrapers/<name>.js` modules are the reference.
- **IMAP rejection sync.** New module `job_pipeline/inbox_sync.py` using `imapclient` or `imap-tools`. Matches rejection-email patterns to applied jobs in your `pipeline_items` table and transitions them to your `rejected` state. This is a huge UX win — most pipelines never close the loop on outcomes.
- **Deterministic post-processing caps on LLM scores.** Right now your `domain_fit.py` is presumably trust-the-LLM. Add a layer that enforces rules like "any role requiring more YOE than I have caps at score 3" so a chatty model can't override your filters.
- **Application complexity classifier.** Tag each job `simple`/`complex` in `summarize.py` so your service router knows whether to send it to the browser-use auto-apply path (simple) or the human-review queue (complex).
- **Voice-check.** Add to `cover_letter_gen.py` — flag em-dashes, "leverage," "synergy," "passionate about" etc. before sending the draft to a human.

### 6. Pickle-Pixel/ApplyPilot — 6-stage pipeline architecture mirror

- Repo: https://github.com/Pickle-Pixel/ApplyPilot
- Stars: 12 · **License: AGPL-3.0** · Language: Python · First release Feb 2026
- Why it matters: this is the closest peer to what you've built — 6-stage pipeline (Discover → Enrich → Score → Tailor → Cover Letter → Auto-Apply), uses JobSpy + 48 Workday portals + 30 direct career sites, Gemini scoring, Claude Code + Playwright MCP for the actual auto-apply step.

**Warning: AGPL-3.0 license.** If you copy code, you must release your whole pipeline under AGPL. Since you're MIT today and this is "a personal project I built to automate my own job hunt," AGPL is probably fine *if you don't intend to ever turn it into a SaaS*. If you ever might, treat this repo as **read-only inspiration** — do not paste their code.

**What to borrow as ideas:**
- The **6-stage architecture explicitly mapped to CLI commands** (`applypilot init/run/apply/status/dashboard`). Your `svc_*` functions already do this — consider exposing them as a CLI matching this shape.
- The **48 Workday employer registry** (`config/employers.yaml`) and **30+ direct career sites** (`config/sites.yaml`) — the lists themselves are facts, not code, and tell you which companies' Workday portals are worth scraping.
- The **dry-run mode for auto-apply** (`applypilot apply --dry-run` fills forms without submitting). You already have a "stops before submit" pattern; add an explicit dry-run flag in your service layer.
- **Parallel workers** for both discovery and auto-apply (`--workers N`). Your pipeline is currently sequential — worth threading.
- **Score threshold override** at runtime (`--min-score 8`). Cheap to add to your service layer.

### 7. feder-cr/Jobs_Applier_AI_Agent_AIHawk — the famous LinkedIn auto-applier

- Repo: https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
- License: MIT (core); some plugins were removed for copyright reasons
- Why it matters: the most-publicized OSS auto-applier (covered by TechCrunch, Wired, The Verge). Most of its weight is LinkedIn-specific, so for you it's mostly useful as a sanity-check / pattern source for question-answering against unusual application questions (the "Do you require sponsorship?" / "Are you authorized to work in X?" / multiple-choice screening questions).

**What to borrow:** their answer-bank pattern — a YAML/JSON file of your standard answers to common screening questions, plus an LLM fallback for novel ones. Your `application_assets.json` already exists for assets; mirror it with an `application_answers.json` for Q&A.

---

## Tier 3 — niche, optional

### 8. IliaLarchenko/Interviewer — AI mock interviewer

- Repo: https://github.com/IliaLarchenko/Interviewer
- License: MIT · Language: Python
- Streamlit-based mock interviewer with Whisper voice input, Mixtral backend, both text and voice. If you ever want to bolt an interview-prep stage onto your pipeline once a job hits the `interview` state, this is the reference.

**Where it'd plug in:** new module `job_pipeline/interview_prep.py`, triggered when you record an outcome of `phone_screen` or `interview` in `service.svc_outcome()`. Feed it the job description + your tailored resume + recent company news, and have it generate a 30-min role-specific mock interview.

### 9. sliday/resume-job-matcher — single-script Claude/OpenAI resume scorer

- Repo: https://github.com/sliday/resume-job-matcher
- License: MIT · Language: Python
- A ~single-file reference for how to do JD-vs-resume scoring with Anthropic Claude or OpenAI's API. Useful as a much lighter alternative to Resume-Matcher if you don't want to take the whole framework.

### 10. amruthpillai/Reactive-Resume — alternative to Canva entirely

- Repo: https://github.com/amruthpillai/Reactive-Resume
- License: MIT · Self-hostable
- If Canva ever becomes painful (OAuth refresh, rate limits, template drift), Reactive-Resume is the most popular OSS resume builder you can self-host and drive via its API.

---

## Recommended integration order

A pragmatic sequence — each step is independently shippable.

**Week 1 — broaden ingestion.** Add `python-jobspy` as a dep, write `job_pipeline/sources/jobspy_source.py`, wire it into `ingest.py`. Test that LinkedIn + Google Jobs + Glassdoor + ZipRecruiter come through your normalize/dedupe correctly. You've now got 5 new sources behind the same API as the rest.

**Week 2 — ATS scoring.** Pull Resume-Matcher's keyword-extraction and similarity code into `job_pipeline/ats_score.py`. Add an `ats_score` column to `pipeline_items`. Have `summarize.py` populate it. Surface it in the Streamlit dashboard alongside your existing `domain_fit` score. Add deterministic post-processing caps in the style of jakemercure28 (cap by YOE mismatch, salary mismatch, location mismatch).

**Week 3 — broader sources.** Re-implement Ashby, Workable, Wellfound, RemoteOK scrapers in Python under `job_pipeline/sources/`, modeled on jakemercure28's Node versions. Each is small.

**Week 4 — auto-apply layer.** Add `browser-use` as a dep, build `job_pipeline/auto_apply/browser_agent.py`. Tag jobs `simple`/`complex` in `summarize.py` (jakemercure28 pattern). Route `simple` + `approved` jobs through browser-use in dry-run mode first; commit to actual submission only after a week of clean dry-run runs.

**Week 5 — close the loop.** IMAP rejection sync (`job_pipeline/inbox_sync.py`) — auto-transition `submitted` items to `rejected` when matching emails arrive. RenderCV as a fallback PDF path. Voice-checker on cover letters.

**Later — polish.** Interview-prep stage (IliaLarchenko/Interviewer pattern) triggered on `interview` state transitions. Worker parallelism (`--workers N`) for discovery and auto-apply. Add a `--min-score N` flag to the FastAPI server.

---

## What to ignore

A few common-but-bad bets worth naming so you don't waste time evaluating them:

- **Most LinkedIn-only Easy Apply bots** (EasyApplyJobsBot, LinkedIn-Easy-Apply-Bot, EasyApplyBot, etc.). You already have a working Playwright LinkedIn path. `browser-use` generalizes beyond LinkedIn — better investment.
- **TypeScript/Node-only repos** for code-lifting. Read for ideas (jakemercure28 is the example) but don't try to bridge stacks.
- **AGPL-licensed code** (ApplyPilot) for direct copying. Read it, don't paste it, unless you're OK relicensing.
- **AIHawk's third-party-LinkedIn paid plugins.** They were removed for copyright reasons. The MIT core is fine; the rest, skip.
- **"OSINT email finder" repos** for recruiter outreach. Most are abandoned or rely on dead APIs. If you want recruiter outreach, pay for a real provider (Hunter.io, Apollo) and skip the OSS ones.

---

## License summary

| Repo | License | Safe to vendor? |
|---|---|---|
| JobSpy | MIT | yes |
| Resume-Matcher | Apache 2.0 | yes |
| browser-use | MIT | yes |
| rendercv | MIT | yes |
| jakemercure28/job-search-automation | MIT | yes (different stack — re-implement) |
| ApplyPilot | **AGPL-3.0** | **no** (read-only inspiration) |
| AIHawk | MIT | yes (core only) |
| Interviewer | MIT | yes |
| sliday/resume-job-matcher | MIT | yes |
| Reactive-Resume | MIT | yes |

Your repo is MIT today. Mixing MIT + Apache 2.0 is fine. AGPL is contagious — avoid.
