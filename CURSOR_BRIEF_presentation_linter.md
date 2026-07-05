# Cursor brief — verify + complete the deterministic presentation linter

## What this adds and why
Objective, rule-shaped defects (skill capitalization, banned phrases, semicolon-
packed bullets, generic "Remote candidate" summaries, informal cover-letter
phrasing) were being left to the LLM critique loop, which is non-deterministic — it
catches a different subset each run. That is the root cause of the build-to-build
score oscillation and the "obvious" misses. The fix is a deterministic linter that
runs the SAME way every time and gets the **last word** before export.

Subjective quality (tone, persuasion, relevance) stays with the critique loop.
Truthfulness stays with `truth_classifier` / `evidence_db` / `named_requirements`.
**The linter never overrides a truth gate — it only changes how a permitted claim
is worded.**

## Already created (your job is to verify, test, and finish)
- **`job_pipeline/style_rules.yaml`** — single machine source of truth: canonical
  casing map, acronyms, prose proper-noun allow-list, synonym/dedupe map, banned-
  phrase lexicons (hype / vague / hedge / informal / cliche / ai_tells / generic
  openers / groveling), phrase replacements, bullet rules, summary rules, skills
  caps, punctuation, cover-letter rules, cross-document checks, parser checks,
  per-rule severity map, penalty weights. **Add new rules HERE, not in code.**
- **`job_pipeline/presentation_linter.py`** — consumes the YAML. Public API:
  `load_rules()`, `lint_resume(content, *, job_title, jd_text)`,
  `lint_cover_letter(content, *, company, role, jd_text)`,
  `cross_document_consistency(resume, cover_letter, *, role, company)`,
  `presentation_penalty(findings)`. Returns a `LintResult(content, findings,
  notes, penalty)` with `.blocking`. Defensive: never raises into a build; falls
  back to embedded rules if pyyaml/YAML is unavailable.
- **`STYLE_GUIDE.md`** — the human-readable companion to the YAML.
- **`requirements.txt`** — added `pyyaml>=6.0`.
- **Wiring (defensive, already inserted — verify it):**
  - `resume_optimizer.py`: imports `lint_resume as _lint_resume_presentation`;
    after the critique loop / grammar pass and **before** `gate_passed`, it runs the
    linter on `working`, applies autofixes, subtracts `penalty` from
    `score["total"]`, and adds `presentation_penalty` / `presentation_blocking` to
    the returned `optimization` dict. Wrapped in try/except.
  - `cover_letter_optimizer.py`: imports `lint_cover_letter as
    _lint_cl_presentation`; just before `content["_optimization"] = {…}` it runs the
    linter and records `presentation` = `{penalty, blocking}`. Wrapped in try/except.

## Verify these specifically
1. `python -c "import job_pipeline.presentation_linter as p; p.load_rules(force_reload=True)"`
   loads the real YAML (not the embedded fallback — check no "using embedded
   fallback" warning is logged).
2. `python -m py_compile job_pipeline/presentation_linter.py job_pipeline/resume_optimizer.py job_pipeline/cover_letter_optimizer.py`.
3. Run a real `make_resume.py` build and confirm the optimization notes now contain
   `presentation[fixed] …` / `presentation[warn] …` lines, and that the skills line
   comes out consistently Title-cased and de-duplicated.
4. Confirm the **clean** path is a no-op: a well-formed resume should get penalty
   0.0 and zero autofixes (no false positives).

## TODO #1 — turn the rubric gate into an enforced regenerate loop (the real consistency lever)
Today `resume_optimizer.run_resume_optimization_pipeline` computes
`gate_passed = score["total"] >= opt_min_score()` but **nothing reads it** — the
docstring even says "never blocks export by default." That is why quality floats.
Implement an actual loop:

```
attempts = 0
while attempts < RESUME_OPT_MAX_REVISIONS (default 2):
    run optimization passes (incl. critique loop) + presentation linter
    score = score_resume_rubric(...) - presentation_penalty
    if score["total"] >= opt_min_score() and not presentation_blocking:
        break
    # targeted revise: feed the failing rubric dimensions + the linter's
    # warn/block findings back into one more critique pass, then re-lint.
    attempts += 1
```

- Gate on BOTH `score >= min` AND `no presentation BLOCK findings`
  (`comma_salad_summary`, `cl_missing_company`, `cl_missing_role`, `glyph_garbage`,
  missing company/title).
- Make blocking enforcement env-toggleable: `RESUME_OPT_GATE_BLOCK=1` to actually
  refuse export below threshold, default `0` (warn) so nothing breaks on day one.
- Add `RESUME_OPT_MAX_REVISIONS` (default 2) to bound cost.
- The presentation linter must be the LAST mutation in each iteration so it always
  has the final word, then re-score.

## TODO #2 — (optional) promote presentation to a real rubric dimension
Right now presentation is applied as a penalty subtracted from the existing 100-pt
total (chosen to avoid rebalancing and breaking existing tests). If you want it to
be a first-class dimension, in `rubric_scorer.py` add `_W_PRESENTATION = 10`, rebalance
(`readability 15→10`, keep the rest), accept an optional `presentation_findings`
arg, and compute the component from warn/block counts. Update the existing rubric
tests' expected totals if you do this.

## TODO #3 — tests (add under `tests/`)
One fixture per rule class so a regression can never silently return:
- `test_presentation_casing`: mixed-case skills → canonical Title Case; acronyms stay upper.
- `test_presentation_synonym_dedupe`: `ticketing`/`ITSM`/`Ticketing / ITSM` → single `Ticketing/ITSM` (assert NO `Ticketing/ITSM/Ticketing/ITSM` re-expansion).
- `test_presentation_semicolon_split`: one semicolon bullet → two bullets.
- `test_presentation_summary_retitle`: `Remote candidate …` → `<title> candidate …`.
- `test_presentation_banned_phrases`: leveraged→used (autofix); help-desk-adjacent, world-class, team player → warn.
- `test_presentation_study_tag`: `Wireshark (study)` dropped unless JD mentions Wireshark.
- `test_presentation_cover_letter`: generic opener + groveling → warn; missing company → block.
- `test_presentation_cross_doc`: resume `3+ yrs` vs CL `5+ yrs` → `cross_doc_yoe_mismatch`.
- `test_presentation_clean_noop`: a clean resume/CL produces penalty 0.0, zero autofixes (false-positive guard).
- `test_presentation_no_crash`: malformed/empty content returns input unchanged with a note.

## Hard constraints
- **Truth layer wins.** The linter rewords/recases/flags; it must never invent or
  inflate a claim. Keep it cosmetic.
- **Never crash a build.** All entry points already swallow exceptions and return a
  note; preserve that.
- **Edit the YAML, not the code,** to change bans/casing/synonyms/thresholds.
- **JSON wire formats unchanged** (resume `summary/experience/skills/projects`;
  cover letter `proof_targets/opening/body_paragraphs/closing`).
- The linter intentionally only re-cases an **unambiguous** prose proper-noun
  allow-list — do NOT add ambiguous common words (Word, Excel, Teams) to
  `prose_proper_nouns`.

## How to validate end to end
Rebuild the Sarnova/Digitech and TopDog packages and confirm:
- Skills line is Title-cased, de-duplicated, capped, no `(study)` padding.
- Summary opens with the target title; no "Remote candidate".
- No semicolon-packed bullets; no `help-desk-adjacent`; no `break/fix work`.
- Cover letter names the company + role, no generic opener / groveling closer.
- Optimization notes show the `presentation[…]` lines.
- A clean rebuild is a no-op (penalty 0).
- With `RESUME_OPT_GATE_BLOCK=1`, a deliberately broken draft regenerates instead
  of exporting.
