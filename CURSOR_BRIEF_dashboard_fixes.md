# Cursor brief: fix the job pipeline dashboard (low queue volume + UX)

## Symptom observed on the live dashboard
The Pending Review queue looks nearly empty. Root cause is NOT broken ingest. The
dashboard reports "276 jobs ingested but not summarized yet ... Currently 6 pending
review, 19 auto-filtered." So two things are starving the queue: a large
summarization backlog that has to be cleared by hand 25 to 50 at a time, and a very
high auto-filter rate (about 19 of 25, ~76%) on what does get summarized. Fix the
items below in priority order. Do not touch the resume/cover-letter presentation
work (presentation_linter.py, style_rules.yaml, judge_anchors/). Keep every claim in
the truth layer intact.

---

## Fix 1 (highest impact): drain the summarization backlog automatically
Today `job_dashboard.py` only exposes "Summarize next batch (up to 50)" which
processes a small batch per click. 276 pending means 6 to 11 manual clicks, and it
rebuilds on every ingest.

- In `job_dashboard.py`, add a **"Summarize ALL pending"** button next to the
  existing one. It should loop the existing batch-summarize call until the
  unsummarized count reaches 0, showing a Streamlit progress bar and a running count,
  with a hard safety cap (for example stop after N batches or a max-minutes budget)
  and a visible cancel/stop.
- Surface the unsummarized count as a metric at the top of the Queue tab so the
  backlog is always visible.
- Add an equivalent CLI path (or make the existing Daily Run actually drain to 0) so
  this can be scheduled instead of clicked.
- Acceptance: one click takes the unsummarized count from 276 to 0 with progress
  feedback, and the queue then populates.

## Fix 2: loosen and instrument the auto-filter
Config currently has `matching.auto_close_pass_verdict_combined_below: 0.48` and
`filters.search_preferences.honor_auto_close: true`, plus the recent score-clamp and
years-gap tuning (commit 1b9442d). Together they close ~76% of summarized jobs.

- In `job_pipeline_config.json`: lower `auto_close_pass_verdict_combined_below` from
  0.48 to about 0.40, and set `search_preferences.honor_auto_close: false` for now
  so preferences only soft-rank (multiplier) instead of hard-closing. Leave
  `auto_close_combined_below: 0.26` as the junk floor.
- In `summarize.py`, record the specific close reason per job (threshold vs
  search_preferences vs years-gap vs salary vs location) in `summary_json`, and add
  per-reason counters the dashboard can display.
- In `job_dashboard.py`, show an auto-filtered breakdown by reason (the "closed
  sub-bucket label" work already started), so the cause is visible, not guessed.
- Acceptance: after a full re-summarize, the auto-filter rate is well under 76%, and
  the dashboard shows how many jobs each rule closed.

## Fix 3: investigate the near-zero ATS scores
Every queued job shows ATS 0 to 8%. Some is real (federal job descriptions are
verbose, so Jaccard overlap is low), but a literal 0% on IT support roles for an IT
support candidate suggests the candidate resume text is not loading into the overlap.

- In `ats_score.py`, verify `build_canonical_resume_text` actually loads
  `consolidated_profile.{md,json}` (and any referenced resume PDFs). If that text is
  empty or missing, log a clear warning and skip the ATS term instead of scoring 0.
- Add a one-time debug print of the canonical resume text length and a sample
  overlap for one known-good IT support posting.
- If ATS is contributing ~0 to every job, it is dragging combined scores down and
  feeding Fix 2. Confirm the blend weight and denominator are correct.
- Acceptance: ATS scores on well-matched IT roles land in a sane range (roughly 20
  to 60%), not 0 to 8% across the board, and a missing-profile case logs a warning
  rather than silently scoring 0.

## Fix 4: fix the queue inversion (best-fit roles filtered, weak ones surfacing)
The Pending Review queue is all government IT Specialist roles (USAJobs) at 30 to 44%
fit, with clearance and federal-application friction. The remote "IT support"
(Indeed) and "desktop support" (jobspy) roles the candidate actually targets are not
appearing. They are either still in the backlog or being auto-closed while weaker
federal roles pass.

- After Fix 1 and Fix 2, re-check the queue. If remote IT support / desktop support
  roles still do not surface, the scoring is keeping the wrong jobs.
- Use the per-job source + close-reason data from Fix 2 to find where the remote
  roles are dying. Adjust `search_preferences` and the seniority/years-gap logic so
  remote first-line support roles are not over-penalized, and so clearance-required
  federal roles are down-ranked (not promoted).
- Acceptance: after a full summarize, the top of Pending Review is dominated by
  remote IT support, desktop support, help desk, and service desk roles, not federal
  clearance roles.

## Fix 5: move the destructive "Clear all jobs" control out of harm's way
The "Clear all jobs / wipe the entire queue" control sits in the sidebar behind only
a checkbox and a 10-second countdown, next to everyday controls.

- Move it into a collapsed "Danger zone" expander at the very bottom of the sidebar
  (or a separate Settings page), and require a typed confirmation (for example typing
  DELETE) in addition to the countdown.
- Acceptance: the wipe control is not adjacent to daily controls and cannot fire
  without an explicit typed confirmation.

## Fix 6 (minor): the "Unknown" status noise
Every card shows a "Unknown" status field. Either populate it (applied / outcome
status) or hide the chip when the value is unknown, so it stops adding noise.

## Constraints
- Do not modify presentation_linter.py, style_rules.yaml, or judge_anchors/.
- Keep changes minimal and consistent with existing code style.
- Truth layer (truth_classifier, evidence_db, named_requirements, light_exposure)
  stays authoritative.
- Add tests under `tests/` for the new summarize-all loop and the ATS empty-profile
  guard.
- No em dashes or en dashes in any user-facing UI strings (project style rule).

## How to validate end to end
1. Click "Summarize ALL pending" and confirm the unsummarized count goes to 0 with
   progress feedback.
2. Confirm the auto-filter rate drops below ~50% and the dashboard shows a
   close-reason breakdown.
3. Confirm ATS scores are in a sane range and a missing profile logs a warning.
4. Confirm remote IT support / desktop support roles now lead Pending Review.
5. Confirm the wipe control is relocated and requires a typed confirmation.
