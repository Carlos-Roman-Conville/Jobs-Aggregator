# Cursor task — improve AI resume + cover-letter generation quality

## Context
This repo (`AI-job-application-pipeline`) tailors a resume + cover letter per job using LLMs
(Gemini for writing via `job_pipeline/genai_client.py`; OpenAI for job scoring in `summarize.py`).
Recruiter-style critiques of generated packages keep flagging the same quality gaps. Your job is to
improve the **generation logic** so future packages score higher, while staying **strictly truthful**.

## Already implemented — DO NOT redo, but stay consistent with these
- `job_pipeline/cover_letter_tailor.py` — cover-letter prompt already rewritten with: (a) "proof targets"
  extraction (identify 6-10 JD requirements, address only truthfully-supported ones BY NAME),
  (b) company-voice / culture matching, (c) an anti-boilerplate phrase ban, (d) a no-location-contradiction
  rule, (e) a no-repetition rule. The JSON wire format is `proof_targets/opening/body_paragraphs/closing`.
- `job_pipeline/rendercv_export.py` + `job_pipeline/resume_export.py` — `clean_skill_items()` already
  sanitizes skill lists (drops orphan "(user-level)" fragments and empty entries, de-dupes). Reuse it.
- `job_pipeline/ats_score.py` — ATS scoring already recalibrated (overlap coefficient + plural/qualifier-
  tolerant keyword matching).
- `job_pipeline/db.py` — duplicate-posting bug fixed in `upsert_posting`.
- `job_pipeline/genai_client.py` — `generate_content_with_retry` (backoff + Gemini model fallback) exists.
- `job_dashboard.py` — a "Rebuild package" button in the Package Ready tab already exists.

## What to build (resume side — the outstanding gaps)

### 1. JD named-requirement detection -> truthful surfacing OR gap flag  (highest priority)
Per job, detect the JD's NAMED / required skills, tools, and responsibilities, then either surface
matching real experience or record a structured gap. Requirements seen repeatedly in critiques:
- **User account management**: onboarding/offboarding, account creation, access provisioning, password
  resets, permission/group management, account deactivation.
- **Active Directory**, **Microsoft 365**, **MacOS**, **MFA / SSO**, **PST / time-zone hours**,
  ticketing/ITSM tools (Freshdesk, Zendesk, Jira Service Management), VPN, video conferencing,
  document management systems.

Behavior:
- If `job_pipeline/consolidated_profile.json` / `job_pipeline/career_master.md` TRUTHFULLY supports a
  requirement, ensure the tailored resume surfaces it explicitly **by name** (summary, a bullet, or skills).
- If NOT supported, record it as a structured **gap** — never fabricate.
- Implement in `job_pipeline/resume_tailor.py` (tailoring prompt) and extend `job_pipeline/resume_gaps.py`
  (gap detection). Surface the detected gaps in `job_dashboard.py` (Manual application / Queue) so the user
  can choose to add them.

### 2. Anti-hyperbole / credibility rules in the resume tailor
The resume produced inflated lines like "Revolutionized personal job search by creating a scalable,
automated system" — reads AI-generated for a helpdesk role. In `job_pipeline/resume_tailor.py` prompt:
- Ban hype ("revolutionized", "transformed", "cutting-edge", "world-class", "synergy", "single-handedly").
- Project/impact lines must be factual and concrete. Target phrasing for the AI-pipeline project:
  "Built a modular Python-based job-application pipeline for job discovery, scoring, resume tailoring, and
  application tracking — demonstrating practical automation, API usage, and workflow design."
- Quantify only with real numbers from the profile; never invent metrics.

### 3. Honest "light exposure" handling
Allow truthful, calibrated phrasing for skills with limited-but-real experience, e.g. "Microsoft 365 and
basic Active Directory exposure, including user/account support and access troubleshooting" — but ONLY when
the profile explicitly marks that skill as light/partial exposure.
- Add a `light_exposure` list to the profile schema (`consolidated_profile.json` / `career_master.md`).
- Teach `resume_tailor.py` and `cover_letter_tailor.py` to use it. It MUST NOT override the existing
  HONEST LIMITS / "do NOT claim" / "Never touched" constraints.

## Hard constraints
- **Truthfulness first**: never fabricate skills, employers, dates, certifications, or metrics. Respect the
  existing HONEST LIMITS handling in the prompts.
- Don't break the JSON wire formats the exporters expect (resume: `summary/experience/skills/projects`;
  cover letter: `proof_targets/opening/body_paragraphs/closing`).
- Add/extend unit tests under `tests/` for new logic (gap detection, light-exposure gating, anti-hype).
- Keep changes minimal and consistent with existing code style.

## Round 2 refinements (from the strongest TopDog package so far, scored 8.25/10)
The skills sanitizer, anti-hype, and JD-surfacing wins above are confirmed working in a live rebuild.
Three concrete polish items remain:

### R2.1 Curate / cap the skills list (it's still padded)
The technical skills line runs 40+ items and includes low-signal study tools (e.g. `Wireshark (study)`,
`Sysinternals Suite (study)`, `Sandboxie-Plus (study)`, `Cisco Packet Tracer (study)`). Helps ATS but reads
as padding to a human. In `job_pipeline/resume_tailor.py`:
- Cap the technical list to roughly the 18-22 most JD-relevant skills for the target role.
- Down-rank or drop `(study)` / `(learning)`-tagged items unless the JD specifically calls for them.
- Prefer skills that map to the JD's proof targets; keep the result truthful (no inventing).

### R2.2 No vague filler verbs in the cover letter
Flagged line: "I have supported users across Google Workspace and **leveraged** Microsoft 365 in past roles."
Add to the `cover_letter_tailor.py` ban/avoid list the vague verbs "leveraged", "utilized", "spearheaded";
require concrete phrasing instead, e.g. "I have supported users in Google Workspace and Microsoft 365
environments, including productivity, access, and collaboration workflows."

### R2.3 Gate "user account management" wording to what's truthfully supported
The resume asserted "Proven ability to **manage user accounts**." Only use that strong wording when the
profile truthfully backs account creation / access provisioning / password resets / deactivation. If the
profile only supports onboarding docs + user support, the tailor must downgrade to softer, accurate wording
like "Familiar with user account support, onboarding workflows, and access-related troubleshooting." This is
the same truthfulness gate as R1/light-exposure — JD-keyword surfacing must never inflate the claim beyond
what the profile supports.

## Round 3 refinements (build-to-build consistency)
Across rebuilds the package oscillates ~8.0-8.25 — tone vs keyword coverage trade off run to run. These
rules reduce the avoidable swings:

### R3.1 Never echo the JD's required experience range
A cover letter said "I have **3-5 years** of experience" — that's the *posting's requirement*, not the
candidate's fact. The tailor must state the candidate's ACTUAL figure from the profile ("3+ years" / "over
three years"), never the JD's required band. Add to `cover_letter_tailor.py` (and resume summary logic).

### R3.2 Curate projects by role-relevance, cap the count
Resume grew to 3 projects (AI Job-Application Pipeline, The Organizer, Art Pipeline). For a help desk role
that's noise. In `job_pipeline/resume_tailor.py`: keep only the 1-2 projects most relevant to the target
role; drop tangential ones. (Same relevance-curation principle as the skills cap in R2.1.)

### R3.3 Ground project mentions — no jargon tangents
A cover letter referenced an "architectural pivot in my personal AI job-application pipeline project" —
impressive to engineers, a tangent for a legal help desk. Frame personal projects as transferable support
habits, e.g. "I build personal automation tools, including a Python job-application pipeline — the same habit
I bring to support work: spotting repeated friction, documenting it, and improving the workflow." Extend the
anti-hype rule to also avoid insider jargon when the audience is non-technical.

### R3.4 Surfaced keywords belong in the skills list, not only the summary
Microsoft 365 appeared in the summary but dropped out of the skills list on one build. Any JD requirement the
tailor decides to surface (and that the profile supports) should appear consistently in BOTH the summary and
the skills section, so ATS keyword scans don't miss it.

## How to validate
Rebuild the Solve Education and TopDog Law packages and confirm:
- Account-management and Active Directory requirements are either surfaced (if truthfully supported) or
  listed as gaps.
- No hype language; the projects section reads factually.
- Cover letters still respect the proof-target/culture/anti-boilerplate rules already in place.
- Skills list is curated (~18-22 items, no padded `(study)` tools unless JD-relevant).
- No vague verbs ("leveraged"/"utilized") in the cover letter; Microsoft 365/tool claims are concrete.
- "Manage user accounts"-style wording appears only when the profile truthfully supports it; otherwise the
  softened phrasing is used.
