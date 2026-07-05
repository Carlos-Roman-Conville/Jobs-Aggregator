# Judge anchors (calibration examples)

The quality judge (`job_pipeline/quality_judge.py`) scores presentation using real examples from this folder.

## Folder layout

```
judge_anchors/
  resumes/           ← resume anchors (.md only)
  cover_letters/     ← cover letter anchors (.md only)
  _archive/          ← retired anchors (ignored by loader)
  _TEMPLATE.rationale.json
```

Legacy files in the root (`ANCHOR_*`, `CL_ANCHOR_*`) are **not** loaded. Only `resumes/*.md` and `cover_letters/*.md`.

## Filename pattern (required)

```
<order>_<tier>_<score>_<slug>.md
```

| Part | Meaning | Examples |
|------|---------|----------|
| order | Sort hint for humans (1=worst, 4=best) | `1`, `2`, `3`, `4` |
| tier | Calibration band | `bad`, `weak`, `mid`, `nearmiss`, `target` |
| score | Your numeric score | `3.0`, `7.6`, `9.4` |
| slug | Company + role, underscores | `VytlOne_Service_Technician_I` |

Examples:

- `1_bad_3.0_template_kitchen_sink.md`
- `2_mid_7.6_VytlOne_Service_Technician_I.md`
- `4_target_9.4_polished_v12.md`

The loader sorts anchors by **score** when building the judge prompt.

## Sidecar rationale (recommended)

Keep the `.md` file as **pure document text** (what a recruiter sees).

Add a paired sidecar (same stem, different extension):

```
4_target_9.0_VytlOne_Service_Technician_I.md
4_target_9.0_VytlOne_Service_Technician_I.rationale.json
```

Copy `_TEMPLATE.rationale.json` and fill in:

- `rationale.chatgpt` / `rationale.claude` — why **this** example got this score
- `key_strengths` / `key_weaknesses` — bullet observations (not generic rules)
- `target_company`, `target_role`, `jd_context` — job traceability

**Do not** put rationale inside the `.md` file. The judge treats the whole `.md` as the document.

`.docx` files in this folder are ignored by the loader (fine for your own reference).

## How many anchors?

One strong example per tier is enough. Extra examples at the same tier are OK (e.g. two `mid` files for before/after on the same role).

## Workflow

1. Apply manually, fix the resume/cover letter until you are happy with the score.
2. Save clean text as `.md` with the naming pattern above.
3. Add `.rationale.json` sidecar with ChatGPT/Claude reasoning + full job posting.
4. Optional: move replaced anchors to `_archive/`.

Use **ChatGPT/Claude** to draft rationale text. Use **Cursor (this repo)** to place files, fix naming, wire loader, and verify tests.

## What to send Cursor for each example

Paste a bundle like this (one message per resume or cover letter):

```
TYPE: resume | cover_letter
SCORE: 7.6/10
TIER: mid          (bad | weak | mid | nearmiss | target — I can infer from score if you skip)
JUDGED BY: chatgpt | claude | both
SLUG: VytlOne_Service_Technician_I   (optional — I can derive from company/role)

--- JOB (paste everything from the URL) ---
[paste full posting here]

--- DOCUMENT ---
[paste final resume or cover letter text]

--- RATIONALE (optional but helpful) ---
[paste ChatGPT/Claude explanation of the score]
```

I will create `{order}_{tier}_{score}_{slug}.md` plus the paired `.rationale.json` with `job_posting`, `source_url`, `scores`, and rationale.

## Env

- `RESUME_OPT_JUDGE=1` — enable judge (default on when anchors exist)
- `JUDGE_ANCHORS_DIR` — override anchor root path
