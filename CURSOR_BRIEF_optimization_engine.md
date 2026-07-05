# Cursor brief — turn the generator into an optimization engine (8.5 → 9.5)

## Mindset shift
Stop treating this as a "tailored resume generator" and make it a **controlled optimization pipeline**:
evidence-grounded generation + validation gates that run BEFORE the PDF is exported. Less raw generation,
more truth-controlled optimization (evidence mapping, truth-safe keyword insertion, parser validation,
anti-fluff cleanup, recruiter-style critique).

## Already built — extend, do NOT rebuild
- `job_pipeline/llm_provider.py` `generate_json` (OpenAI primary + Gemini fallback).
- `job_pipeline/named_requirements.py` — vague-verb, JD-years-echo, project-jargon detectors + light-exposure.
- `CURSOR_BRIEF_resume_quality.md` R1-R3: JD requirement surfacing/gap, skills cap (~18-22), project curation,
  no-vague-verbs, truthful account-management gating, surfaced-keyword-in-both-summary-and-skills (R3.4).
Build the new layers on top of these; reuse their helpers.

## NEW components, phased by ROI

### Phase 0 — Integrity guards (cheap, deterministic, ALWAYS-ON; build these first)
Real correctness bugs observed in a Sarnova/Digitech build (the TopDog build was clean — same system, this is
variance on the *bug* axis). These must run on every export regardless of full-opt mode; most need NO LLM.

**0.1 Cross-job duplicate-metric / misplaced-bullet detector (credibility-critical).** A real build placed
"reduced guest transition wait time 75% (10->2.5 min)" and "supported 20-30 staff" under BOTH BEAT THE BOMB
AND "Newport Beach Investments / Acropolis Motor Inn (2016)" — fabricating that the 75% win happened at a 2016
hotel job. Fix: pin every metric/bullet to its owning employer in the evidence DB (1.1); a metric may appear
under ONLY that employer. Flag/strip any metric or near-identical bullet that surfaces under a second job.

**0.2 Garbage / malformed-line detector.** Catch non-grammatical AI artifacts like the real summary line
"...aligned with mission, communication, customer while delivering supported experience in Ticketing / ITSM,
PST / time-zone coverage." Heuristics: comma-salad with no verb, dangling slashes, strings of JD-keyword
fragments glued together, requirement labels leaking into prose. Reject and regenerate that field; never export
a field that fails the check.

**0.3 Semantic skills deduper (extends `clean_skill_items`).** `clean_skill_items` only drops exact/orphan
dupes; this build still shipped "Ticketing / ITSM", "ticketing", "Help desk ticketing", "ITSM" as separate
items. Add a canonicalization/synonym map that merges concept variants to one phrase (-> "Ticketing/ITSM").

**0.4 Intra-job duplicate-bullet detector.** Within a single job, collapse near-duplicate bullets (e.g.
"Supported 20-30 staff per shift" vs "Supervised 20-30 team members on shift") to one.

These four are the floor: an integrity pass before export would have caught every bug in that Sarnova package.

### Phase 1 — Foundation: evidence + truth (everything else depends on it)
**1.1 Master evidence database.** Add a structured evidence store (e.g. `job_pipeline/evidence.json`, or a
section in `consolidated_profile.json`) keyed by employer, with `systems / support / networking /
documentation / metrics / truth_limits`. Example:
```json
{ "BEAT_THE_BOMB": {
  "systems": ["Windows workstations","Linux Photon servers","NUC kiosks","CCTV","RFID","DMX","Dante audio"],
  "support": ["Tier 1-2 support","ticketing","RustDesk remote administration","iOS/Android support"],
  "networking": ["TCP/IP","DNS","Ethernet troubleshooting"],
  "documentation": ["SOPs","runbooks","onboarding documentation"],
  "metrics": ["reduced guest transition wait time 75% (10 -> 2.5 min)"],
  "truth_limits": ["No formal Active Directory administration unless separately confirmed"] } }
```
Resume/cover-letter generation must pull facts ONLY from this + the profile — never invent. (User populates it.)

**1.2 Truth-safe keyword resolver (4-way).** For each JD requirement classify against the evidence DB:
`direct_proven` → use confidently; `adjacent_true` → phrase carefully; `learnable` → only as "currently
learning" and only if role-relevant; `not_true` → omit. Map to wording. Extend `named_requirements.py` /
`resume_gaps.py`. Example: don't write "Managed user accounts and authentication tools" unless `direct_proven`;
if adjacent, write "Supported user onboarding workflows, access-related troubleshooting, and MFA/SSO issue
resolution."

### Phase 2 — Structure
**2.1 Role thesis.** Generate ONE controlling thesis sentence per job first (e.g. TopDog: "Remote help desk
candidate who troubleshoots under pressure, communicates clearly, documents fixes, and improves workflows as
the company scales"). Every section must support it; cut bullets that don't.

**2.2 Bullet-type balance.** Per main job enforce a mix: ~2 troubleshooting/support, 1 tools/systems,
1 documentation/process, 1 quantified impact, +1 leadership/communication if relevant.

**2.3 Metric bank.** Store candidate metrics in the evidence DB and inject the best **2-4 per role** (not just
one). Known metrics: 75% wait-time reduction (10->2.5 min); supported 20-30 staff/shift; coordinated 50+
frontline personnel; medical assessments in ~35% of allotted time; ~15% lost-job-rate reduction. Truthful only.

**2.4 Skills compression (extends R2.1).** Technical 18-24 items, soft 8-12; drop `(study)` tools unless the JD
asks; prioritize JD must-haves. Cut Docker/Git/Python unless the JD mentions scripting/automation (keep Python
only if the project section uses it).

### Phase 3 — Validation gates BEFORE export (the consistency lever)
**3.1 Scoring rubric pass.** Score the draft 0-100 before final PDF:
coverage 25 / proof 25 / readability 15 / quantified impact 10 / no-fluff-no-overclaim 10 /
company alignment 10 / formatting-parser 5. Require a threshold (e.g. **>= 90**) to export; below it, auto-revise
or surface the gaps. (This is the generate-and-gate step that tames build-to-build variance.)

**3.2 Red-flag / anti-fluff detector (extends existing bans).** Flag + replace AI-ish/inflated lines:
"revolutionized", "confident in my ability", "enthusiastic interest", "perfectly aligns", "leveraged",
JD-range echo ("3-5 years"), "architectural pivot". Prefer concrete verbs: built, supported, resolved,
documented, reduced, coordinated, improved, maintained.

**3.3 ATS parser sanity pass.** After PDF render, re-extract text and assert: company names readable, job
titles readable, dates attached to the correct job, bullets not broken, no garbage glyphs (e.g. "￾"), no
duplicated text, skills don't wrap into nonsense. Fix the BEAT THE BOMB heading to a boring single-line
structure that always parses: `BEAT THE BOMB — Technical Operations Manager` / `Philadelphia, PA | Sept 2024 –
Mar 2026` (don't rely on columns the parser scrambles).

**3.4 Two-pass recruiter + ATS review.** Before export, run:
(a) skeptical-recruiter pass — "find anything exaggerated, vague, AI-generated, irrelevant, risky, or
mismatched; revise for credibility/concision/alignment; add NO unsupported claims"; then
(b) ATS-optimizer pass — "identify missing must-have keywords, add only if truthfully supported, keep
human-readable and under two pages."

### Cover letter
**4.1 Structured voice mirroring (extends existing culture matching).** Extract from the JD: 3 culture words,
3 technical requirements, 1 business outcome, 1 mission hook; build the opening hook from them.
(TopDog: ownership/impact/scaling + Tier 1-2/M365/MFA-SSO + protect remote-workforce productivity + help build
the firm's technical foundation as it scales.)

## Target pipeline order
1. Parse JD → must-have + preferred reqs, culture signals, business outcome.
2. Pull candidate proof from the evidence DB / profile.
3. Classify proof: direct / adjacent / learnable / unsupported (1.2).
4. Generate role thesis (2.1).
5. Build sections around the thesis; enforce bullet balance (2.2) + inject metrics (2.3).
6. Compress skills (2.4).
7. Strip AI/fluff (3.2).
8. Score against rubric; gate or revise (3.1).
9. Render PDF → ATS parser check (3.3).
10. Skeptical-recruiter + ATS review passes (3.4).
11. Export resume + cover letter.

## Hard constraints
- **Truthfulness/evidence-only:** never exceed the evidence DB or its `truth_limits`; respect existing
  HONEST LIMITS. The whole system's credibility rests on this.
- **Cost/latency:** these gates add several LLM calls per build (score + 2 review passes + possible regen).
  Fine at your low volume, but make the heavy passes **toggleable via env** (e.g. `RESUME_OPT_FULL=1`) so a
  quick build can skip them. Cache the JD parse + evidence pull within a build.
- JSON wire formats unchanged; resume stays < 2 pages.
- Add tests under `tests/` for each new gate (truth classifier, scorer threshold, ATS parser asserts).

## What needs YOU (Carlos), not Cursor
The single biggest lever is **populating the master evidence DB accurately**, including the `truth_limits`
(e.g. whether you actually did Active Directory administration / account creation / password resets, or only
adjacent support). Cursor builds the machinery; only you can supply truthful evidence — and the truth-control,
the 4-way classifier, and the recruiter pass are only as good as that data.
