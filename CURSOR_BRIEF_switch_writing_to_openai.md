# Cursor task — switch resume + cover-letter generation from Gemini to OpenAI

## Goal
Move the **writing/generation** LLM calls from Gemini to OpenAI (ChatGPT models), because output tone/
naturalness is the recurring weakness and GPT-4-class is stronger there. Keep Gemini as an automatic
**fallback** so a transient OpenAI outage never strands a build (we already got burned by Gemini 503s).
Do NOT change the prompt *content* — the proof-targets, anti-hype, truthfulness/HONEST-LIMITS, company-voice,
and Round 1-3 rules stay exactly as written; only the model backend changes.

## Context (what already exists — reuse these patterns, don't reinvent)
- `job_pipeline/summarize.py` already calls OpenAI: `from openai import OpenAI`, key from
  `OPENAI_API_KEY` / `CHATGPT_API_KEY`, model `gpt-4.1-mini` via `OPENAI_JOB_SUMMARY_MODEL`,
  `client.chat.completions.create(...)`, with retry envs `OPENAI_SUMMARY_MAX_RETRIES` /
  `OPENAI_SUMMARY_RETRY_BASE_SEC`. Mirror this style.
- `job_pipeline/genai_client.py` has `generate_content_with_retry` (backoff + `is_retryable_capacity_error`
  + Gemini model fallback). Build the OpenAI equivalent with the same shape.
- Writing tasks currently on Gemini (see `job_pipeline/genai_settings.py:gemini_model_for`):
  `tailor` (resume), `cover_letter`, `bootstrap`, `gaps`, `career`, `package_check`.
- Job scoring/summarization is ALREADY OpenAI — leave it alone.

## What to build

### 1. New `job_pipeline/openai_client.py`
- `openai_generate_json_with_retry(*, model, system, user, label="openai", max_retries=None, base_sleep=None) -> dict`
- Uses `client.chat.completions.create` with `response_format={"type": "json_object"}` so structured output
  comes back as clean JSON.
- Same retry/backoff semantics as `genai_client.generate_content_with_retry` (retry on 429/500/502/503/504,
  "rate limit", "overloaded", timeouts; exponential backoff).
- Reads key from `OPENAI_API_KEY` or `CHATGPT_API_KEY`. Raise a clear error if missing.

### 2. Provider selection + cross-provider fallback
- Add a small resolver (e.g. in `genai_settings.py` or a new `llm_provider.py`):
  - `LLM_WRITING_PROVIDER` env: `openai` (default) | `gemini`.
  - `openai_model_for(role)` mirroring `gemini_model_for(role)`, env-overridable per role
    (`OPENAI_RESUME_TAILOR_MODEL`, `OPENAI_COVER_LETTER_MODEL`, `OPENAI_RESUME_BOOTSTRAP_MODEL`,
    `OPENAI_RESUME_GAP_MODEL`, `OPENAI_CAREER_MODEL`, `OPENAI_PACKAGE_CHECK_MODEL`), then a global
    `OPENAI_WRITING_MODEL`, defaulting to **`gpt-5.5`** (OpenAI's current flagship as of 2026-05; use
    `gpt-5.4` for a cheaper/faster balance, `gpt-5.4-mini` for high volume). VERIFY the exact model ID against
    the live catalog — https://developers.openai.com/api/docs/models — and confirm whether the chosen model
    uses the **Responses API** vs Chat Completions and supports `response_format` JSON mode. Do not hardcode a
    guessed string; read the current models list.
- A single wrapper the writing modules call, e.g. `generate_json(role, system, user)` that:
  1. tries the primary provider (`OPENAI_WRITING_PROVIDER`),
  2. on a retryable/total failure, falls back to the OTHER provider automatically,
  3. returns parsed JSON (keep `_parse_json_object` as a safety net).

### 3. Route the writing modules through it
Update these to call the new provider-agnostic wrapper instead of `generate_content_with_retry` directly,
keeping their existing prompts verbatim:
- `job_pipeline/resume_tailor.py` (resume tailoring)
- `job_pipeline/cover_letter_tailor.py` (cover letter)
- `job_pipeline/bootstrap_resume_profile.py` (profile consolidation)
- `job_pipeline/resume_gaps.py` (if it makes an LLM call)
- `job_pipeline/package_build.py` `consistency_check_llm` (package sanity check)
- `career_understanding.py` (career role), if it uses the Gemini path

Note: the cover-letter/resume prompts are currently built as one big string for Gemini. Split into a short
`system` instruction + the `user` payload for the chat API, but preserve all the rules and the JSON-keys spec.

## Hard constraints
- Prompt *content* unchanged — do not weaken truthfulness/HONEST-LIMITS, proof-targets, anti-hype, or the
  R1-R3 rules in `CURSOR_BRIEF_resume_quality.md`.
- JSON wire formats unchanged (resume: `summary/experience/skills/projects`; cover letter:
  `proof_targets/opening/body_paragraphs/closing`).
- Never put secrets in code; read keys from env / `.env` only.
- Add tests under `tests/` for `openai_client` and the provider selection/fallback (mirror
  `tests/test_genai_client.py`).
- Keep it minimal and consistent with existing style.

## Config the user sets in `.env`
```
OPENAI_API_KEY=sk-...            # already present (scoring uses it)
LLM_WRITING_PROVIDER=openai      # openai (default) | gemini
OPENAI_WRITING_MODEL=gpt-5.5     # current flagship (2026-05); or gpt-5.4 / gpt-5.4-mini. Confirm exact ID.
```

## How to validate
- With `LLM_WRITING_PROVIDER=openai`, rebuild the TopDog and Solve packages; confirm resume + cover letter
  generate correctly, JSON parses, and tone reads more natural.
- Flip `LLM_WRITING_PROVIDER=gemini` and confirm the old path still works (so you can A/B).
- Kill OpenAI access (bad key) and confirm it falls back to Gemini without crashing the build.
- Confirm OpenAI usage shows up at https://platform.openai.com/usage after a build.
