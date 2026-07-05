# Job Pipeline Autofill (Firefox + Chrome)

One-click autofill for job application forms (Indeed, iCIMS, Greenhouse, Lever, etc.) using your **`consolidated_profile.json`** data.

## Install in Firefox (2 minutes)

```powershell
powershell -ExecutionPolicy Bypass -File browser_extension\install_firefox.ps1
```

Or manual:

1. Open **`about:debugging`**
2. Click **This Firefox** → **Load Temporary Add-on…**
3. Select **`browser_extension/manifest.json`** in this repo

> Temporary add-ons unload when Firefox restarts. Reload the same way after restart, or sign/package for permanent install later.

## Install in Chrome (2 minutes)

```powershell
powershell -ExecutionPolicy Bypass -File browser_extension\install_chrome.ps1
```

Or manual:

1. Open **`chrome://extensions`**
2. Toggle **Developer mode** on (top-right)
3. Click **Load unpacked** → pick the **`browser_extension`** folder

> Unlike Firefox temporary add-ons, Chrome unpacked extensions persist across restarts. You'll see a yellow "Developer mode extensions disabled" banner each launch — dismiss it; the extension still works.

### If Chrome shows a manifest error

The bundled `manifest.json` includes both Firefox and Chrome background-script keys for portability. Modern Chrome (~v114+) loads it with a warning. Older Chrome may reject the unknown `scripts` key with `'scripts' is not allowed for type:manifest_version:3`.

If that happens, swap in the Chrome-pure manifest:

```powershell
Copy-Item -Force browser_extension\manifest.chrome.json browser_extension\manifest.json
```

Then hit **Reload** on the extension card in `chrome://extensions`. To go back to Firefox, restore `manifest.firefox.json` over `manifest.json`.

## First-time setup

**Option A — Sync from API (recommended)**

```bash
uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload
```

Extension popup → **Sync profile from pipeline**

**Option B — Import JSON (offline)**

Dashboard sidebar → **Write autofill profile JSON** → **Download autofill_profile.json**

Extension → right-click icon → **Manage Extension** → **Options** → paste JSON → **Import JSON to storage**

## Daily use (one click)

1. Open the job application page in Firefox
2. Upload your tailored resume PDF if the form asks
3. Click the **Job Pipeline Autofill** extension icon
4. Click **Fill this application**
5. Review fields, answer EEO/salary/custom questions, **Submit yourself**

The extension fills contact info, work history, education, and descriptions. It does **not** auto-submit.

## What gets filled

From `job_pipeline/autofill_profile.py`:

- Name, email, phone, city/state (smart phone format: digits-only when the field demands it)
- Each work experience: company, title, dates, description (bullets)
- Education: school, degree, details
- **References:** name, title, company, relationship, email, phone (from `job_pipeline/references.json`)
- **Screening Q&A:** work authorization, sponsorship, citizenship, clearance, relocation, salary, veteran status, EEO, background, "how did you hear" (from `job_pipeline/screening_answers.json`)
- Cover letter (when `profile.cover_letter` is set; otherwise summary fills the summary field, not the cover letter)
- Greenhouse, Lever, Ashby, Workable known name attributes

## Screening answers

Standing answers to the repetitive Yes/No and dropdown questions live in **`job_pipeline/screening_answers.json`**.

Edit values directly — the engine matches each form's question text (legend, heading, aria-label) against rule patterns and clicks the matching radio / picks the matching select option / types the value. Examples:

| Question text on form | Looks up | Default |
|---|---|---|
| "Are you legally authorized to work in the US?" | `work_authorization.authorized_to_work_us` | Yes |
| "Do you require sponsorship now or in the future?" | `work_authorization.requires_sponsorship` | No |
| "Willing to relocate?" | `logistics.willing_to_relocate` | No |
| "Desired salary" | `compensation.desired_salary_number` | 75000 |
| "Veteran status" | `veteran.veteran_status` | I am a protected veteran |

After editing the JSON, re-sync (extension popup → **Sync profile from pipeline**) so the extension picks it up.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Sync failed | Start API on port 8000, or import JSON in Options |
| Filled 0 fields | Reload the application tab after installing the extension |
| iCIMS iframe | Extension scans all frames automatically — try Fill again on the step with employment fields |
| Wrong title on a job | Update `consolidated_profile.json` (bootstrap) and re-sync |

## Files

| File | Role |
|------|------|
| `fill_engine.js` | Field matching + React-safe input events |
| `content.js` | Runs fill in each frame |
| `popup.js` | Fill + Sync buttons |
| `../job_pipeline/autofill_profile.py` | Profile builder from consolidated profile |
| `../job_pipeline/references.json` | Professional references (edit this to add/change refs) |

## References

Edit **`job_pipeline/references.json`** when you add or change references, then re-run:

```powershell
powershell -ExecutionPolicy Bypass -File browser_extension/install_firefox.ps1
```

Or dashboard sidebar → **Write autofill profile JSON**, then extension → **Sync profile from pipeline**.

## API

`GET http://127.0.0.1:8000/autofill/profile` — no API key required (local use).
