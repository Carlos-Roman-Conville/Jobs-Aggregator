# Cursor brief — calibrated quality judge + gate calibration + golden corpus

## Goal
The deterministic presentation linter + gate revise loop now handle every
*objective* defect. What's left for a stable 9 is the *subjective* axis — "is this
compelling / relevant / well-toned for this role?" — which the heuristic
`rubric_scorer.py` can't measure (it counts keyword substrings and lengths). Add a
**calibrated LLM judge** whose score the gate can trust, then **calibrate the
threshold** so the gate actually goes green for good docs, and **surface
`gate_blocked`** so a failing package doesn't silently ship.

Three deliverables, in priority order: (1) the judge, (2) threshold/rubric
calibration backed by a golden corpus, (3) wire `gate_blocked` into the dashboard /
submit path.

---

## Part 1 — `job_pipeline/quality_judge.py` (new module)

A single, calibrated, **read-only** scoring call. It does NOT rewrite content (the
existing `_targeted_gate_revise` does that); it scores and critiques.

### Independence + determinism (both matter)
- **Different model from the writer.** The writer path is provider-ordered and
  often Gemini. The judge must NOT grade its own model's output. Call OpenAI
  directly via `openai_client.openai_generate_json_with_retry(model=..., system=...,
  user=..., temperature=0.0, label="quality_judge")` — do not route through
  `llm_provider.generate_json` (that would let Gemini answer).
- **Temperature 0** and a **fixed prompt + fixed anchors** → stable run-to-run. This
  stability is the whole point; it's what stops the score from oscillating.
- Model: a mid-tier OpenAI model is fine (e.g. `gpt-4.1` or `gpt-4.1-mini`); make it
  configurable via env `RESUME_JUDGE_MODEL`.

### Signature
```python
def judge_quality(
    resume_content: dict,
    *,
    job_description: str,
    job_title: str = "",
    cover_letter_content: dict | None = None,
    rules=None,
) -> dict:
    """Return {'score': 0-100, 'subscores': {...}, 'critique': [str,...],
              'verdict': '7'|'9'|..., 'ok': bool} or {'ok': False} on failure."""
```
- Defensive: wrap in try/except; on any provider error return `{"ok": False}` so the
  gate falls back to the heuristic rubric. Never crash a build.
- Toggle with `RESUME_OPT_JUDGE=1` (default off until calibrated). Cache the result
  within a single build (the gate loop may call it up to 3×; only re-judge after a
  revise actually changed the content).

### What it scores (subjective ONLY)
compelling/impact, JD relevance, tone/professional fit, narrative coherence,
cover-letter persuasion. It must **defer truthfulness to the truth layer**
(`truth_classifier` / `evidence_db` / `named_requirements`) — the judge may *flag* a
suspected overclaim in its critique but must not reward fabrication, and the truth
gate remains authoritative. It should NOT re-litigate the objective items the
presentation linter already owns (casing, banned phrases, etc.).

### Few-shot anchors (this is the calibration — without it the score drifts)
Store 2–3 worked examples in `job_pipeline/judge_anchors.json`, seeded from REAL
packages so the scale is grounded in Rex's actual roles:
```json
[
  {"label": "7", "role": "Service Desk Technician",
   "resume_excerpt": "<a real ~7/10 summary+bullets>",
   "why": "Generic summary, one padded bullet, tone slightly informal."},
  {"label": "9", "role": "Service Desk Technician",
   "resume_excerpt": "<a real ~9/10 summary+bullets>",
   "why": "Names the title, quantified impact, tight relevant bullets, confident tone."}
]
```
Use the strongest TopDog build (~8.25) and a weaker Sarnova build as the 9-vs-7
seeds. Rex supplies/approves these excerpts — they define what "9" means.

---

## Part 2 — wire the judge into the existing gate (do NOT add a second loop)

In `resume_optimizer.run_resume_optimization_pipeline`, the gate loop already lives
at the "Final deterministic PRESENTATION pass + optional gate revise loop" block.
Extend it:

1. After the presentation pass each iteration, if `RESUME_OPT_JUDGE=1`, call
   `judge_quality(...)`.
2. Combine signals — recommended: keep the heuristic rubric as a floor and let the
   judge own the subjective verdict:
   ```
   gate_passed = (
       score["total"] >= opt_min_score()
       and judge.get("score", 100) >= opt_judge_min()    # new env, default 85
       and not blocking_presentation_findings
   )
   ```
   (If the judge is unavailable, drop its term so behavior is unchanged.)
3. Feed the judge's `critique` list into `_targeted_gate_revise` alongside the
   presentation findings — that's richer, more actionable revise feedback than the
   deterministic findings alone.
4. Add `judge_score`, `judge_subscores`, `judge_critique` to the returned
   `optimization` dict next to `gate_passed`/`gate_revisions`.

Keep the same `RESUME_OPT_MAX_REVISIONS` bound. Do the same (optionally) for the
cover letter in `cover_letter_optimizer.py`.

---

## Part 3 — calibrate the threshold (the bug that will bite otherwise)

Today `opt_min_score()` defaults to **90**, but heuristic builds land ~84–87 and the
presentation penalty now subtracts up to **15** more. If a genuinely good doc rarely
reaches 90, the gate fails every build and burns all `MAX_REVISIONS` for nothing —
extra LLM cost + latency with `gate_passed` permanently False.

Fix by **measuring, not guessing**:
- Build a golden corpus: `tests/golden/` with 10–15 saved real packages
  (resume+CL JSON) and, where you have them, the ChatGPT scores Rex recorded.
- Add `tools/judge_eval.py` that runs `judge_quality` (and the heuristic rubric)
  over the corpus and prints, per package: heuristic score, judge score, ChatGPT
  score, and the deltas; plus overall correlation/mean-abs-error between judge and
  ChatGPT.
- Use the results to (a) set `opt_min_score` / `opt_judge_min` to the band that
  separates Rex's real "good" from "needs work" packages, and (b) confirm the judge
  agrees with ChatGPT closely enough to **replace** the manual ChatGPT step. If they
  diverge on a case, that divergence is a new rule to encode (deterministic) or a new
  anchor (judgment).
- Re-run `judge_eval.py` as a regression check whenever anchors/prompts change.

---

## Part 4 — make `gate_blocked` actually do something

`resume_optimizer` sets `optimization.gate_blocked`, but nothing reads it, so
`RESUME_OPT_GATE_BLOCK=1` is currently inert. Wire it:
- In `service.py` package build, propagate `gate_blocked` / `judge_score` /
  `gate_revisions` into `package_meta`.
- In `job_dashboard.py` Package Ready tab, show a clear "⚠ gate blocked (score N,
  M revisions)" badge and the judge critique, and require an explicit manual
  override before a blocked package can be marked approved / submitted.
- In `job_pipeline/auto_apply/*`, never auto-submit a package with
  `gate_blocked=True`.

---

## Hard constraints
- **Truth layer wins.** The judge scores/critiques; it never authorizes a claim the
  evidence DB / truth classifier disallows.
- **Judge is read-only.** Rewrites stay in `_targeted_gate_revise`.
- **Never crash a build.** Judge failure → `{"ok": False}` → gate falls back to the
  heuristic rubric.
- **Cost control.** Judge is OFF by default (`RESUME_OPT_JUDGE=0`), uses the cheaper
  model, temp 0, cached per build, bounded by existing `MAX_REVISIONS`.
- **Wire formats unchanged.** Resume `summary/experience/skills/projects`; cover
  letter `proof_targets/opening/body_paragraphs/closing`.

## Tests (add under `tests/`)
- `test_quality_judge_offline`: with `RESUME_OPT_JUDGE=0`, pipeline behavior is
  identical to today (judge not called).
- `test_quality_judge_defensive`: provider error → `{"ok": False}`, gate falls back,
  no crash.
- `test_judge_anchors_load`: anchors file parses and is non-empty.
- `test_gate_uses_judge`: with a stubbed judge returning a low score, the gate fails
  and triggers a revise; with a high score it passes.
- `tools/judge_eval.py` runs over `tests/golden/` and prints the comparison table.

## How to validate end to end
1. `RESUME_OPT_JUDGE=1` rebuild of Sarnova/Digitech and TopDog: optimization output
   shows `judge_score` + `judge_critique`; the gate now reflects subjective quality.
2. `python tools/judge_eval.py` shows the judge tracking the recorded ChatGPT scores
   within a few points; pick thresholds from it.
3. A deliberately weak draft fails the gate, gets a targeted revise, and improves on
   re-judge.
4. With `RESUME_OPT_GATE_BLOCK=1`, a still-failing package shows the blocked badge in
   the dashboard and cannot be auto-submitted.
