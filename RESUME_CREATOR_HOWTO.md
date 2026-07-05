# Manual resume creator — how to use it

Paste a job description, get a tailored resume PDF (+ markdown). Grounded in your actual PDFs in `/resume/` (merged into one consolidated profile), not a single LinkedIn export.

## Recent behavior (CLI + dashboard)

- **RenderCV 2.x YAML** — `build_tailored_cv_yaml()` emits `cv.sections.<section>` as a **list** of entry objects (matches current RenderCV docs). Legacy `type:` / `entries:` wrappers are gone.
- **`gap_answers` table** — When Postgres is available, non-empty gap answers are saved and reused on later runs (CLI prefills prompts; Streamlit prefills inputs).
- **Stale profile warning** — If `job_pipeline/consolidated_profile.md` is older than **30 days**, `make_resume.py` and the dashboard warn you to re-bootstrap.
- **Streamlit** — Use the **Manual resume** tab in `job_dashboard.py` for the same flow as `make_resume.py` (tailor → gaps → re-tailor → RenderCV PDF).
- **Gap detector** — Hard-requirement captures prefer short phrases (fewer multi-line artifacts); duplicates collapse after normalization.

## One-time setup (run once, then occasionally when you add new resume PDFs)

```
python -m job_pipeline.bootstrap_resume_profile
```

What it does:

- Reads every PDF in `./resume/` (currently 7 readable PDFs: IT 2 resume, Start Resume, resume, Resume 1, Science and Engineering Resume, Resume template 1, Nu OP resume).
- Asks Gemini to merge them into ONE grounded master profile — no fabricated facts, conflicting dates flagged.
- Writes `job_pipeline/consolidated_profile.md` (LLM input) and `job_pipeline/consolidated_profile.json` (structured contact + military service + skills).

Flags:

- `--dry-run` — print what would be written, don't touch disk.
- `--resume-dir "C:/path/to/other/folder"` — point at a different folder.

Requires `GEMINI_API_KEY` or `GOOGLE_API_KEY` in your `.env`.

## Every-job usage

```
python make_resume.py
```

Interactive: prompts you to paste the JD, asks gap-fill questions, drops a tailored PDF + markdown in `generated_resumes/`.

### Faster variants

```
# JD from a file (best for long descriptions):
python make_resume.py --jd-file job.txt --title "IT Support Specialist" --company "Acme"

# Pipe from clipboard (Windows):
type job.txt | python make_resume.py --title "Jr Sysadmin"

# Skip the gap-fill Q&A — just want a quick draft:
python make_resume.py --jd-file job.txt --no-gaps

# Pre-supplied answers (write one answer per line, # for comments):
python make_resume.py --jd-file job.txt --answers-file answers.txt

# Markdown only, no PDF (if you haven't installed rendercv yet):
python make_resume.py --jd-file job.txt --no-pdf

# Heuristic gaps only, skip the LLM gap pass (cheaper, faster):
python make_resume.py --jd-file job.txt --no-llm-gaps

# Just show me the gaps, don't render anything:
python make_resume.py --jd-file job.txt --print-gaps-only
```

### Strategy levels

```
python make_resume.py --jd-file job.txt --strategy conservative
python make_resume.py --jd-file job.txt --strategy balanced     # default
python make_resume.py --jd-file job.txt --strategy aggressive
```

- **conservative** — mirrors the source phrasing, omits anything uncertain.
- **balanced** — emphasizes JD overlap, no invented facts (default).
- **aggressive** — confident framing where the profile clearly supports it, still no fabrication.

## What the gap-fill loop looks like

If the JD asks for "Active Secret clearance" and the profile doesn't mention it, you'll see:

```
1. [!] active secret clearance (high)
   The JD mentions this requirement: active secret clearance. Can you honestly speak to it? (Y/N + one honest line.)
   > 
```

Type `y` (yes, no detail), `n`/`skip` (omit), or a sentence (`"yes, held Public Trust during Army Reserve service"`). Your answers are folded back into the profile for a second tailor pass — so anything you confirm gets reflected in the final resume.

## Output files

Every run drops files in `generated_resumes/`:

- `tailored_0_<company>_<role>.md` — markdown draft you can copy/paste anywhere.
- `tailored_0_<company>_<role>.yaml` — the RenderCV source (debug-friendly).
- `tailored_0_<company>_<role>.pdf` — the final ATS-friendly PDF.

If `rendercv` isn't on your PATH, you'll see `PDF skipped: rendercv_cli_not_on_path` — markdown is still produced.

To install rendercv:

```
pip install "rendercv>=2.0,<2.8"
```

Then invoke the CLI the same way this repo does: `rendercv render path/to/file.yaml`. On some stacks, `rendercv --help` (and Typer help text) crashes; that does not affect `rendercv render`.

`requirements.txt` includes **`markdown`** — RenderCV's CLI imports it while rendering; without it you may still get a PDF but a non-zero exit code.

## Verifying it works

```
# 1. Confirm consolidated profile is present
python -c "from job_pipeline.bootstrap_resume_profile import load_consolidated_profile; p=load_consolidated_profile(); print('name:', p.get('name'), '/ experience entries:', len(p.get('experience') or []))"

# 2. Dry-run on a sample JD
echo "Junior Systems Administrator. 2+ years Linux. PowerShell required. Active Secret clearance preferred." > /tmp/test_jd.txt
python make_resume.py --jd-file /tmp/test_jd.txt --title "Jr Sysadmin" --company "TestCo" --no-gaps --no-pdf
```

You should see:

- Profile source: `consolidated_profile.md`
- Step 1/3 prints a `Markdown draft: ...` path
- The markdown file in `generated_resumes/` contains your tailored content

## Files involved

| File | Role |
|---|---|
| `job_pipeline/bootstrap_resume_profile.py` | Reads all PDFs in `/resume/`, merges into master profile |
| `job_pipeline/consolidated_profile.{md,json}` | Master profile output (treat as the truth) |
| `job_pipeline/resume_tailor.py` | LLM tailoring; `tailor_resume_from_jd()` is the new manual-JD entry |
| `job_pipeline/resume_gaps.py` | Detects requirements not in your profile, generates gap-fill questions |
| `job_pipeline/rendercv_export.py` | Builds RenderCV YAML from tailored content + contact info, runs CLI |
| `make_resume.py` | The CLI you actually run |
| `job_dashboard.py` → **Manual resume** tab | Paste JD, tailor, gap Q&A, PDF (mirrors CLI) |
| PostgreSQL `gap_answers` | Saved gap answers for auto-fill (`job_pipeline/schema.sql` + `job_pipeline/db.py`) |

### Themes

Defaults to **`classic`**; pass `--theme sb2nov`, `--theme moderncv`, or `--theme engineeringresumes` once you confirm what your RenderCV install supports.
