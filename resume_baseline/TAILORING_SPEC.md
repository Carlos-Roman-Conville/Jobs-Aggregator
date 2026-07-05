# Resume tailoring spec

This is the contract between **the LLM tailoring step** (called per JD) and **the rendercv renderer**. The LLM does not produce a full resume — it produces a small JSON **patch** that gets applied on top of the canonical [carlos_jr_sysadmin.yaml](carlos_jr_sysadmin.yaml). The combined result is what gets rendered to PDF.

This file defines the patch shape, the rules the LLM must follow, and the worked example for one fictional JD.

---

## Why a patch, not a full rewrite

Three reasons:

1. **Smaller LLM output** = lower latency, lower cost, fewer hallucination opportunities
2. **Provenance is clear** — we can log the patch alongside the application and audit "did the LLM invent something?"
3. **Hard guardrails are easy** — invalid keys (e.g. inventing a new company) get rejected at patch-apply time, not inside a 1500-line YAML the LLM might silently fabricate

---

## Patch shape

The patch is a single JSON object with up to 5 top-level keys. All are optional — an empty patch (`{}`) just renders the baseline unchanged.

```json
{
  "summary_override": "string | null",
  "experience_bullet_order": {
    "<company_key>": ["<bullet_index>", "<bullet_index>", ...]
  },
  "experience_bullet_drops": {
    "<company_key>": ["<bullet_index>", "<bullet_index>", ...]
  },
  "skills_row_order": ["<label>", "<label>", ...],
  "skills_row_drops": ["<label>", ...],
  "section_visibility": {
    "projects": true,
    "certifications": true
  },
  "metadata": {
    "tailored_for_item_id": 0,
    "tailored_for_jd_keywords": ["string", "string"],
    "model": "gpt-4.1-mini",
    "rationale_short": "string under 200 chars"
  }
}
```

### Field semantics

#### `summary_override` (string or null)
- Replaces the entire `cv.sections.summary[0]` text
- 3–5 lines, plain text, no markdown, no bullets
- **Must** match the Jr Sysadmin (~2 yrs) framing — no claiming Senior, no inventing certs, no claiming AD/cloud/IaC
- **Must** weave in 2–3 keywords from the JD that are honestly supported by the baseline

#### `experience_bullet_order` (object)
- Keys: company key (`beat_the_bomb`, `got_junk`, `army_reserve`)
- Values: array of bullet indices in the desired display order
- Indices reference the **baseline** bullet order (0-indexed)
- Must include every bullet that's not in the corresponding `experience_bullet_drops` list
- LLM **cannot** add new bullet indices (only reorder existing ones)

#### `experience_bullet_drops` (object)
- Same key shape as `experience_bullet_order`
- Lists bullets to **omit** for this JD (e.g. drop the 2 ops-leaning BEAT THE BOMB bullets for a pure IC role)
- Hard cap: cannot drop more than 50% of bullets in any role
- Hard cap: cannot drop *all* bullets — every role must have at least 1 bullet visible

#### `skills_row_order` (array)
- Reorders the `technical_skills` rows by their `label`
- Labels not listed get appended in their baseline order

#### `skills_row_drops` (array)
- Removes a skills row by `label` (e.g. drop "AV & Streaming" if the JD is enterprise IT with no AV scope)
- Hard cap: cannot drop more than 3 of the 8 baseline rows

#### `section_visibility` (object)
- Boolean toggle for `projects` and `certifications` sections
- Other sections (summary, experience, education, technical_skills) are **always visible** — cannot be toggled off

#### `metadata` (object)
- Provenance only — never affects rendering
- `tailored_for_item_id` links back to the pipeline DB row
- `tailored_for_jd_keywords` is the LLM's view of "what this JD wanted"
- `rationale_short` is one human-readable sentence: "Why I made these changes"

---

## What the LLM cannot do

The patch shape **structurally prevents**:
- Inventing new bullets (no `bullet_additions` key exists)
- Inventing new companies, titles, dates, or degrees
- Adding fictional certifications
- Toggling off the summary, experience, or education sections
- Renaming the candidate or changing contact info

The patch-apply layer also **validates**:
- Every dropped bullet index actually exists in the baseline
- Every kept-and-reordered bullet index actually exists
- No company has all bullets dropped
- No more than 50% of bullets dropped in any role
- No more than 3 of 8 skills rows dropped

If validation fails, the patch is **rejected** and the baseline is rendered unchanged with a warning logged on the pipeline row.

---

## Worked example

**JD (fictional):** "Junior Linux Systems Administrator, fully remote, healthcare startup. Required: Linux server administration, TCP/IP and DNS troubleshooting, ticketing system experience, on-call rotation comfort. Nice-to-have: Bash scripting, RustDesk or comparable remote tooling, documentation discipline."

**LLM output (the patch):**

```json
{
  "summary_override": "Junior Systems Administrator with ~2 years of hands-on Linux server administration, TCP/IP and DNS troubleshooting, and remote endpoint management. Comfortable on-call across a 24/7 production environment, with a documented track record of authoring SOPs that cut new-hire onboarding from 2 weeks to 5 days. Background in operations management and military medic training brings strong incident response under pressure.",
  "experience_bullet_order": {
    "beat_the_bomb": [0, 1, 2, 4, 3, 5],
    "got_junk": [0, 1],
    "army_reserve": [0, 1]
  },
  "experience_bullet_drops": {
    "beat_the_bomb": [6, 7]
  },
  "skills_row_order": [
    "Operating Systems",
    "Networking",
    "Remote Administration",
    "Scripting & Config",
    "Documentation",
    "Hardware",
    "Business Systems",
    "AV & Streaming"
  ],
  "skills_row_drops": [],
  "section_visibility": {
    "projects": true,
    "certifications": true
  },
  "metadata": {
    "tailored_for_item_id": 4271,
    "tailored_for_jd_keywords": ["linux", "tcp/ip", "dns", "ticketing", "on-call", "bash", "documentation"],
    "model": "gpt-4.1-mini",
    "rationale_short": "Tech-only IC role; dropped venue ops bullets, surfaced documentation skill row, kept projects."
  }
}
```

**What the patch did:**
- Rewrote the summary to lead with Linux admin + TCP/IP + on-call comfort + documentation
- Reordered BEAT THE BOMB bullets to put Linux + networking first, moved support work after troubleshooting
- Dropped the 2 ops-leaning bullets (75% wait time reduction, 8-person team training) since this is a pure IC role
- Reordered skills to surface OS → Networking → Remote → Scripting → Documentation first
- Kept projects (tech-heavy interview likely)
- Kept certifications visible (will be empty until earned)

---

## Implementation notes for the next coding session

When we build the actual tailor module (probably `job_pipeline/resume_tailor_v2.py`):

1. **Apply step** is pure Python — load baseline YAML → apply patch → write `<item_id>_tailored.yaml`
2. **LLM prompt** includes: baseline YAML (truncated to relevant sections), JD text, scoring card from the pipeline row, this spec
3. **Validation** runs before render — reject + log on any rule violation
4. **Render call** = `subprocess.run(["rendercv", "render", "<tailored.yaml>"])`
5. **Storage:** save patch JSON next to PDF for audit; let the dashboard show the patch in the package_ready view
6. **DB:** add `tailored_resume_patch_json` and `tailored_resume_pdf_path` columns to the pipeline_items table

---

## Open questions

- Do we want a **second LLM pass** to write a *cover paragraph* using the same patch metadata? (Probably yes — same call.)
- Should the patch be allowed to **append a project** if the JD explicitly asks for "personal projects" experience? (Suggest no — provenance gets murky. Keep projects fixed in baseline; add new ones only by editing the baseline file.)
- Where do we keep the rendered PDFs? Suggested: `outputs/per_item/<item_id>/resume.pdf`
