# Cursor brief — finish wiring `search_preferences.md` into the pipeline

## Context (read this first)

A new grounding document and Python module were just added:

- `job_pipeline/search_preferences.md` — hand-edited authority for SEARCH +
  SCORING preferences. Mirrors the precedence pattern of `career_master.md`
  (it does NOT govern resume tailoring). The user edits this file by hand
  to change what kinds of jobs the pipeline prioritizes and auto-closes.
- `job_pipeline/search_preferences.py` — thin parser + scorer. Exports
  `load_search_preferences()` (cached) and
  `score_posting_against_preferences(posting_dict)`. The .md is the
  source of truth; the .py is a parser, not a duplicate.
- `job_pipeline/summarize.py` was wired so the scoring chain is now:
  `base → ×domain → ×seniority → ×location → ×preferences → combined`.
  `pref_reject` joins `loc_reject` / `sal_close` / `auto_close` in the
  hard-close decision. A new `search_preferences` block is written onto
  each card alongside `domain_fit` and `location_policy`.

The preferences scorer returns:
```python
{
  "pref_multiplier": float,            # default 1.0
  "auto_close_reason": str | None,     # title_avoided / salary_below_floor / outside_metro / noise_filtered
  "boost_signals": [str, ...],
  "preference_notes": [str, ...],
  "work_mode": "remote" | "hybrid" | "onsite" | "unknown",
  "distance_miles_from_19107": float | None,
  "salary_low_usd": int | None,
  "salary_floor_applied": int,
}
```

The proximity-to-19107 rule (closer == better) is encoded in the .md
"Geography constraints" distance table and applied as a graduated
multiplier on top of the hybrid/onsite work-mode multiplier:
0–5 mi → 1.10x, 5–10 mi → 1.07x, 10–20 mi → 1.04x, 20–30 mi → 1.01x,
>30 mi → hard reject.

The scoring layer is live; what's missing is dashboard visibility,
operator tooling, ingest-search-term wiring, config toggles, docs,
and tests. Those are your job.

---

## Ground rules (don't break these)

1. **`search_preferences.md` is the authority.** Never duplicate its
   values into Python constants. If you need a value, parse it out of
   the .md via `load_search_preferences()`. The only thing in the .py
   that lives "as code" is the regex patterns and the proximity miles
   table — and even those are documented as mirroring the .md.
2. **The .md must survive `bootstrap_resume_profile`.** Like
   `career_master.md`, the bootstrap process should not touch it.
3. **Precedence stays:** `search_preferences.md` governs SEARCH and
   SCORING. `career_master.md` and `consolidated_profile.md` govern
   resume tailoring. Do not cross the streams — do not start using
   `search_preferences.md` inside `resume_tailor.py`.
4. **Auto-close from preferences is a hard reject**, same kind as
   `loc_reject`. Items are set to status `closed` with
   `filter_reason="search_preferences:<reason>"`. Don't downgrade this
   to a soft signal.
5. **Multiplier clamp** in `score_posting_against_preferences` is
   `[0.50, 1.75]`. Don't widen it without discussing — the chain
   `domain × seniority × location × preferences` can otherwise stack
   past 1.0 and saturate the combined score.
6. **Tests live under `tests/`** (create the directory if missing).
   Use plain `unittest` or `pytest` — match whatever the repo already
   uses. Don't add a new test framework.
7. **Bash mount quirk** (only relevant if you run tests from WSL or
   a container against the Windows share): if `__pycache__` looks
   stale and `wc -l` on a .py disagrees with the editor, `touch` the
   .py to bump its mtime past the .pyc. This is a filesystem-share
   delay, not a code bug.

---

## Deliverables (do these in order)

### 1. Dashboard surfacing — `job_dashboard.py`

The Streamlit dashboard at `localhost:8501` already renders the
`location_policy` and `domain_fit` blocks on each card via
`_card_data()`. Mirror that for the new `search_preferences` block.

**Edit `_card_data()`** to pull the new block from `summary_json`:

```python
prefs = sj.get("search_preferences") if isinstance(sj.get("search_preferences"), dict) else {}
```

Add to the returned dict:
```python
"prefs": prefs,
```

**Render the prefs block on the card UI** in the same visual style as
the existing location_policy / domain_fit panels. Show:
- `pref_multiplier` (formatted like `1.39x`)
- `work_mode` (badge: remote / hybrid / onsite / unknown)
- `distance_miles_from_19107` (only if not None — render as
  `~12 mi from 19107` or "remote / N/A" otherwise)
- `salary_low_usd` and `salary_floor_applied` side by side
  (e.g. `$62k offered / $55k floor (remote_flex)`)
- `boost_signals` as a row of small chips
- `preference_notes` as a collapsed expander labeled
  "why these preferences fired"
- If `auto_close_reason` is set: render a red banner with the
  reason text (`title_avoided` → "Title on avoid list",
  `salary_below_floor` → "Below salary floor",
  `outside_metro` → "Outside 30-mile radius of 19107",
  `noise_filtered` → "JD body matched a noise pattern").

**Extend `_format_filter_reason()`** (around line 232 of
`job_dashboard.py`) so `search_preferences:<reason>` reasons get
human-readable labels:

```python
if reason.startswith("search_preferences:"):
    code = reason.split(":", 1)[1]
    return {
        "title_avoided":      "Title is on the hard-reject list",
        "salary_below_floor": "Salary below floor for this work mode",
        "outside_metro":      "Outside 30-mile radius of 19107",
        "noise_filtered":     "JD body matched a noise pattern (1099/MLM/AI trainer/etc.)",
    }.get(code, f"Search preferences: {code}")
```

**Add a sidebar transparency panel** (under existing profile/consolidated
sections — see lines ~700–740). Show what `load_search_preferences()`
parsed from the .md: salary floors, radius, seed terms. Title it
"Search preferences (loaded from `search_preferences.md`)" with a
"Reload" button that calls `load_search_preferences(reload=True)`.

**Acceptance:** open dashboard, find an item that was scored with the
new module, verify the prefs panel renders with multiplier, distance,
boosts, and notes. Find a closed item with
`filter_reason="search_preferences:title_avoided"` and verify the
filter-reason text is human-readable.

---

### 2. Rescore CLI — `job_pipeline/rescore_preferences.py`

Mirror `job_pipeline/rescore_domain.py` line-for-line. Same eligibility
list (`pending_review`, `ranked`, `approved`, `package_ready`), same
batch entrypoint, same `update_item_fit_domain_rescore` style of write
(or add a sibling `update_item_fit_preferences_rescore` in `db.py` if
the existing function would clobber unrelated fields).

The rescore must:
1. Re-parse summary_json.
2. Take `fit_score_after_domain_then_seniority` (or recompute it from
   base / domain / seniority if missing).
3. Apply `evaluate_location_policy` exactly as before to get
   `combined_after_location`.
4. Call `score_posting_against_preferences()` with the same posting
   dict shape that summarize.py uses:
   ```python
   {
     "title": row["title"],
     "description_text": row["description_text"],
     "location": row["location"],
     "salary_text": row["salary_text"],
     "source": str(row.get("source") or ""),
   }
   ```
5. Compute `combined_after_preferences` the same way summarize.py does.
6. Update `summary["search_preferences"]`, `summary["fit_score_after_location"]`,
   `summary["fit_score_blended"]`, `summary["score_explanation"]` fields,
   and `summary["filter_reason"]` if the prefs result rejects.
7. If `auto_close_reason` is set, the item must be moved to status
   `closed` with `filter_reason="search_preferences:<reason>"`.

Add a CLI entrypoint (mirror `rescore_domain.py`'s `__main__` block):

```bash
python -m job_pipeline.rescore_preferences           # all eligible items
python -m job_pipeline.rescore_preferences --id 4218 # single item
python -m job_pipeline.rescore_preferences --dry-run # preview only
```

**Acceptance:** run against the existing review queue and confirm
that any items violating the new preferences (Senior titles, NYC
onsite, etc.) get auto-closed with a `search_preferences:*` reason,
and any items that pass get a new `pref_multiplier` applied to their
combined score.

---

### 3. Ingest search-term wiring

Currently `job_pipeline_config.json` hardcodes search terms for each
source (e.g. `sources.jobspy.search_term: "desktop support technician"`,
`sources.usajobs.keyword: "IT Specialist"`). The .md now owns the
seed list under "Search-term seed list".

**Add to `job_pipeline/search_preferences.py`** (new helper —
non-breaking, both APIs stay):

```python
def search_term_seeds() -> List[str]:
    """Convenience: return the parsed seed list, empty list if absent."""
    return list(load_search_preferences().get("search_term_seeds") or [])
```

**Refactor each ingest source** (`job_pipeline/sources/jobspy_source.py`,
`usajobs_source.py`, `feeds_source.py`, and `hn_whoishiring.py`,
plus `job_pipeline/apify_indeed.py`) so they:

- If the source's config block in `job_pipeline_config.json` has a
  `use_search_preferences_seeds: true` flag, fan out one ingest call
  per seed term from `search_term_seeds()`.
- Otherwise keep current behavior (use the hardcoded `search_term` /
  `keyword` field — backwards compatible).

Add the toggle to `job_pipeline_config.json` for each source:
```json
"use_search_preferences_seeds": false
```

Default to `false` so existing behavior is preserved. The user can
turn it on per source. When the flag is true and the seed list is
empty, fall back to the existing hardcoded value with a logger warning.

**Acceptance:** flip `sources.jobspy.use_search_preferences_seeds`
to `true`, run a dry-run ingest, and confirm the source iterates
over `["desktop support", "IT support", "help desk", ...]` instead
of just the single hardcoded term.

---

### 4. Config toggle + JSON cleanup — `job_pipeline_config.json`

Add a top-level filters block:

```json
"filters": {
  "min_salary_usd": 0,
  "location_policy": { ... existing ... },
  "search_preferences": {
    "enabled": true,
    "honor_auto_close": true,
    "apply_multiplier": true
  }
}
```

**In `summarize.py`**, gate the `score_posting_against_preferences` call
on `filters.search_preferences.enabled`. When disabled, set
`pref_multiplier = 1.0` and `pref_reject = False` and skip the call.
When `honor_auto_close: false`, still compute the multiplier but
ignore `auto_close_reason`. When `apply_multiplier: false`, do the
opposite (still honor auto-close but treat multiplier as 1.0).

This gives the operator three orthogonal knobs without touching the .md.

**Acceptance:** flip `enabled: false`, run `python -m job_pipeline.rescore_preferences`,
confirm every item gets `pref_multiplier=1.0` and no items are
closed for `search_preferences:*` reasons.

---

### 5. README + system docs

In `README.md`, add a section (near where `career_master.md` is
documented) titled "Search preferences (`search_preferences.md`)"
that explains:

- File purpose (hand-edited authority for search/scoring).
- Precedence: `search_preferences.md` (search/scoring)
  ⟷ `career_master.md` (tailoring) — separate authorities, do not
  mix.
- How to edit (just open and hand-edit; the parser is forgiving).
- The four hard-reject reasons it can fire.
- How to enable/disable via `job_pipeline_config.json`.
- How to re-apply to existing items: `python -m job_pipeline.rescore_preferences`.

In `SYSTEM_DESIGN_AND_ROADMAP.md`, add a paragraph to the scoring
section describing the new stage in the chain
(`base → domain → seniority → location → preferences → combined`).

**Acceptance:** a new contributor reading the README understands
when to edit `search_preferences.md` vs `career_master.md`.

---

### 6. Tests — `tests/test_search_preferences.py`

Create the directory if it doesn't exist. Use whatever framework the
repo uses (check for `pytest.ini`, `pyproject.toml`, or existing
test files first). Required cases — these are the exact assertions
the smoke test verified live, copy them verbatim:

| Case | Input title / loc / salary | Expected |
|---|---|---|
| `remote_tier1_60k` | "IT Support Specialist" / "Remote (US)" / "$62,000–$70,000/yr" with ladder + cert | `auto_close_reason is None`, `pref_multiplier ≈ 1.50`, `boost_signals` includes `remote_1.25x`, `tier1_title_family_1.15x`, growth signals |
| `hybrid_center_city` | "Desktop Support Technician" / "Philadelphia, PA" / "$66,000" | `pref_multiplier ≈ 1.39`, includes `hybrid_in_metro_1.10x`, `tier1_title_family_1.15x`, `proximity_inner_core_0_5mi_1.10x` |
| `onsite_kop_tech_75k` | "NOC Technician" / "King of Prussia, PA" / "$75,000" | `pref_multiplier ≈ 1.22`, includes onsite-tech-metro and outer-ring proximity |
| `onsite_nyc_reject` | "Help Desk Analyst" / "New York, NY" / "$72,000" | `auto_close_reason == "outside_metro"` |
| `noise_ai_trainer` | "AI Trainer" / "Remote" / "$30/hr" | `auto_close_reason == "title_avoided"` (title hits first) |
| `senior_avoid` | "Senior Systems Administrator" / "Remote" / "$150,000" | `auto_close_reason == "title_avoided"` |
| `hybrid_wilmington` | "Help Desk Technician" / "Wilmington, DE" / "$68,000" with cert reimbursement | passes; multiplier ≈ 1.30; outer-ring proximity |
| `usajobs_vet` | "IT Specialist" / "Philadelphia, PA" / "$78,000" / source=usajobs | passes; multiplier includes `vet_lane_1.08x` |
| `remote_low_salary` | "IT Support Technician" / "Remote" / "$45,000" with no growth signals | `auto_close_reason == "salary_below_floor"` |
| `hourly_36` | "Desktop Support" / "Philadelphia, PA" / "$36 per hour" | salary annualized to 74,880; passes onsite gate; multiplier ≈ 1.33 |
| `remote_56k_growth_flex` | "Junior Sysadmin" / "Remote" / "$56,500" with cert + mentorship | passes (flex floor $55k); multiplier ≈ 1.50 |

Plus one parser test: `load_search_preferences()` returns a dict with
the right keys and `salary_floors == {"remote":60000,"remote_flex":55000,"hybrid":65000,"onsite":70000}`.

**Acceptance:** `pytest tests/test_search_preferences.py -v` passes.

---

### 7. Streamlit preferences-debug page

Add a new Streamlit page or expander labeled "Preferences debug" that
lets the user paste a title, location, salary, and JD body and renders
the live `score_posting_against_preferences()` output (multiplier,
auto_close_reason, boost_signals, notes). This is so they can
sanity-check changes to the .md without round-tripping through
ingest+summarize.

Place it near the existing "Manual JD" section if there is one;
otherwise add a new sidebar entry.

**Acceptance:** paste a Senior title in, see the red `title_avoided`
banner immediately. Paste a Philly NOC role at $80k, see a
multiplier above 1.20 and the boost chips.

---

### 8. One known issue worth fixing

In the smoke test, the federal USAJOBS "IT Specialist" case was
classified as `work_mode=unknown` because the JD body didn't say
"remote" / "hybrid" / "onsite". Federal postings are usually onsite
or "telework eligible" without those exact words. Two options:

- **Easy:** when `source == "usajobs"` and `work_mode == "unknown"`,
  treat as onsite for geography purposes (so the 30-mile rule still
  applies and we don't accidentally pass a Kansas City IT Specialist
  through with no geo check).
- **Better:** scan the JD for `"telework eligible"`, `"remote work eligible"`,
  `"100% remote"`, `"located at"` and improve `classify_remote_hybrid_on_site`
  in `location_policy.py` to handle these federal phrasings.

Pick one. Document the choice in the code.

---

## Out of scope (do not touch)

- `resume_tailor.py`, `cover_letter_gen.py`, `package_build.py` — the
  prefs file does not govern tailoring.
- `career_master.md` content — not your file to edit.
- `consolidated_profile.md` content — generated by bootstrap.
- The LLM summarizer prompt — keep it as is. Preferences are a
  deterministic post-process; we don't want the model second-guessing
  them.
- Adding new dependencies (geopy, etc.). The proximity table is
  hand-curated; that's the design.

---

## Verification checklist before you call it done

- [ ] Dashboard renders the prefs block on cards and the sidebar
      transparency panel.
- [ ] `_format_filter_reason("search_preferences:title_avoided")`
      returns the human-readable string.
- [ ] `python -m job_pipeline.rescore_preferences --dry-run` prints
      what would change without writing.
- [ ] `python -m job_pipeline.rescore_preferences` updates existing
      items and closes ones that violate the new prefs.
- [ ] At least one ingest source has been refactored to honor
      `use_search_preferences_seeds`.
- [ ] `pytest tests/test_search_preferences.py -v` passes 11+ cases.
- [ ] Toggling `filters.search_preferences.enabled: false` makes the
      pipeline behave as if the preferences module didn't exist.
- [ ] README has a new "Search preferences" section that points at
      `search_preferences.md` as the authority.
- [ ] Streamlit "Preferences debug" panel works end-to-end on a
      hand-typed posting.

---

## Tone / coding style for this codebase

- Functions get docstrings; the docstring opens with one sentence
  saying what the function does. Mirror the style in
  `domain_fit.py` and `location_policy.py`.
- Type hints on public functions (`from __future__ import annotations`,
  then `Dict[str, Any]`, `List[str]`, etc. — match the existing files).
- Logging via `import logging; logger = logging.getLogger(__name__)`,
  not `print`.
- Streamlit code: use `st.expander` for collapsible details, follow
  the existing card layout convention (see how `domain_fit` block is
  rendered today in `job_dashboard.py`).
- No emojis in code.

When in doubt, copy the pattern from `location_policy.py` /
`rescore_domain.py` — those two files are the closest precedents
for what you're building.
