# Agent Apply Handoff — Current Workflow (supersedes the build-package path in MULTI_AGENT_APPLY_RUNBOOK.md)

**Last updated:** 2026-06-17
**Purpose:** Drop a fresh Claude Code agent into Carlos's job-apply workflow with zero re-setup. This is the *actual* hand-built process used 2026-06-14 → 06-17, not the older AgentWorker auto-tailor path.

> **IMPORTANT — what's different from the old runbook:** The `AgentWorker.build_package()` / resume-tailor / cover-letter-generator path in `MULTI_AGENT_APPLY_RUNBOOK.md` is **FORBIDDEN**. Carlos rejected the auto-tailor as slop. Every resume + cover letter is **hand-written as a rendercv YAML and rendered to PDF**. The old runbook's queue mechanics (SKIP LOCKED, status columns) are still valid for dedup; its automated build steps are not.

---

## 0. The three agents (what makes each one different)

You are ONE of three parallel agents. The ONLY thing that differs between agents is **which title family you target** and **how the resume is framed**. Everything else (env, defaults, ATS quirks, honesty rules, dedup) is identical.

| Agent | Target title families | Resume framing | GitHub on resume? |
|---|---|---|---|
| **Tier 1** | Help Desk Analyst/Technician/I, Service Desk Analyst/I, Desktop Support, IT Support I/Specialist, NOC Technician/Analyst L1, Technical Support Specialist I, Customer Support Engineer I (technical) | "Hands-on IT generalist, Tier 1/Tier 2 first-and-only IT at BTB" — lead with helpdesk/ticketing/troubleshooting | YES |
| **Tier 2** | Service Technician II, Support Analyst II, Help Desk Tier 2, NOC Tech II, Jr Sysadmin, Systems Support Technician, Desktop Support II, IT Operations Technician | Same BTB story but emphasize the **sysadmin + escalation + Windows Server/AD/GPO/VLAN/Veeam** depth. Carlos lifted the Tier-1 IC cap — Tier 2 is honest (BTB was Tier 1 AND Tier 2). | YES |
| **Operations** | Operations Manager, Ops Coordinator, Service Manager, Implementation Manager/Specialist, Office Manager, Account Manager, Shift/Team Lead, Customer Success/Onboarding Specialist | Lead with **IT Operations Manager (BTB) + Junior Operations Coordinator (GOT-JUNK) = ~4.5 yrs ops**. De-emphasize deep tech; emphasize people/process/vendor/training/SOP/coordination. | **OMIT** GitHub — lead with LinkedIn only |

**Partition by title so the three agents never apply to the same posting.** Each agent only picks rows whose title matches its family. When in doubt about which agent owns a title, Tier 2 > Tier 1 for "II/2" titles; Operations owns anything Manager/Coordinator/Lead that isn't an IT-IC support role.

---

## 1. Environment & tools

- **Project root:** `E:\AI Programs\AI-job-application-pipeline`
- **Postgres:** host `localhost`, port `5433`, user `postgres`, password `yourpassword`, **db `postgres`** (the `job_pipeline` db name does NOT exist — current_database is `postgres`). Connect via the helper:
  ```bash
  cd "E:/AI Programs/AI-job-application-pipeline" && POSTGRES_PASSWORD=yourpassword POSTGRES_PORT=5433 python -c "
  import sys; sys.path.insert(0,'.')
  from job_pipeline.db import pg_connect
  conn = pg_connect(); cur = conn.cursor()
  ...
  "
  ```
  - **Windows charmap gotcha:** printing JD text with unicode (✅, →, em-dash) crashes cp1252. Use `python -X utf8` AND `.encode('ascii','ignore').decode()` on description text.
- **Dashboard:** `http://localhost:8501` (Streamlit). Buttons: **Ingest Jobs** (~14 min, pulls ~90 postings from 354 sources), **Summarize ALL pending** (red button under the Queue tab counters; ~4 min, scores ingested→pending_review/closed). Reload (F5) after each to see counts.
- **Resume/cover rendering:** `rendercv` (Typst). Command:
  ```powershell
  Set-Location "E:\AI Programs\AI-job-application-pipeline\generated_resumes"
  rendercv render <file>.yaml --pdf-path "$env:USERPROFILE\Downloads\Carlos_Roman-Conville_Resume_<Co>.pdf"
  ```
  PDFs MUST land in `C:\Users\rexoc\Downloads\` so Carlos can upload them.
- **Browser:** Claude in Chrome MCP (`mcp__Claude_in_Chrome__*`). One shared Chrome across all agents. Create your own tab once and pass its `tabId` on every call. Don't touch other agents' tabs.

---

## 2. The core loop (one application)

1. **Pick a candidate** from the DB (query in §6). Title must match your agent's family. Company must NOT already be submitted/skipped (query enforces this).
2. **Dedup gate (DO THIS EVERY TIME — Carlos has called this out twice):**
   - `grep -ri "<company>\|<posting_id>" personal_docs/application_log/`
   - Check the row's current `status` in DB.
   - **If Carlos says "we already did this" — believe him over the log.** Logs have gaps from pre-pipeline sessions.
3. **Read the JD** (DB `description_text`, or open the apply URL in Chrome and `get_page_text`). Run it through the **auto-skip blockers** (§5). If it fails any, mark skipped and move on.
4. **Build resume YAML** — copy the closest prior `generated_resumes/tailored_*.yaml`, edit summary + highlights + skills to mirror the JD's language honestly. Render to Downloads.
5. **Build cover letter YAML** — `cv.sections.cover_letter` list of paragraphs. **First line = today's date** (e.g. "June 17, 2026"). Structure: (1) opening tying BTB to the role, (2) concrete bullet-by-bullet mapping to the JD's responsibilities, (3) an HONEST gaps paragraph, (4) WGU/mission close. Render to Downloads.
6. **Drive Chrome** — open apply URL, fill every field. Use JS injection for Lever/iCIMS (see §7); computer-use clicks for the rest.
7. **Hand off to Carlos** at the human gates (§4): resume upload, cover-letter upload, CAPTCHA, account creation/password, email verification code, final Submit.
8. **On confirmed submit:** update DB `status='submitted'`, append a row to today's session log, tell Carlos it's done, pick next.

**Per-turn discipline:** screenshots on heavy SPA tabs frequently time out ("CDP … timed out"). When that happens, fall back to `read_page` / `get_page_text` / `javascript_tool` — don't retry screenshots in a loop.

---

## 3. Carlos's identity facts (for resume/cover — never invent beyond these)

- **Name:** Carlos D. Roman-Conville · Philadelphia, PA
- **BEAT THE BOMB** — IT Operations Manager — Philadelphia, PA — 2024-09 to 2026-03. First-and-only IT at the flagship venue: Tier 1/Tier 2 troubleshooting, Windows 10/11, Windows Server (DNS), Local GPO, Cisco VLAN via switch web UI, Veeam backups, TeamViewer/RustDesk, Notion ticketing built from scratch, authored every runbook/SOP/KB/training doc, trained every new technical hire, live-event incident response, on-call rotations.
- **1-800-GOT-JUNK** — Junior Operations Coordinator — NJ/DE Region — 2019-04 to 2021-07. On-site estimates, dispatch coordination, in-person negotiation, Salesforce (user-level). Promoted twice.
- **U.S. Army Reserve, 338th Medical Brigade** — 68W Combat Medic — 2012 to 2020. 3 yrs internal instruction. Honorable discharge.
- **WGU** — B.S. Cybersecurity & Information Assurance — Online — enrolled 2026 (in progress).
- **Rowan University** — B.A. Political Science — Glassboro, NJ — enrolled 2016-09, graduated 2019-05 — GPA 3.80, Cum Laude, Pi Sigma Alpha. (Dates confirmed 2026-06-18; always include on education entries.)
- **rendercv header block** (reuse verbatim):
  ```yaml
  cv:
    name: "Carlos D. Roman-Conville"
    email: "cromanconville@gmail.com"
    phone: "+18563979706"
    location: "Philadelphia, PA"
    social_networks:
      - network: "LinkedIn"
        username: "carlos-roman-conville-068781217"
      - network: "GitHub"            # OMIT this entry on Operations resumes
        username: "Carlos-Roman-Conville"
  ```
  (design block at end: `design:\n  theme: "classic"`)

---

## 4. Standard form-fill defaults

| Field | Value |
|---|---|
| Phone | (856) 397-9706 |
| Address | 1229 Chestnut St, Philadelphia, PA 19107 |
| Email | cromanconville@gmail.com |
| LinkedIn | https://www.linkedin.com/in/carlos-roman-conville-068781217 |
| GitHub | https://github.com/Carlos-Roman-Conville (tech roles only) |
| Work authorization | Authorized to work permanently in US for any employer |
| Sponsorship needed | No |
| Relocation | No (remote-first; Philly hybrid/onsite within 30mi OK) |
| Start date | 06/30/2026 |
| Desired salary | Tier1/2: $20-22/hr or $55-70K. Ops: $65-70K. Always "within your posted range" if a range is shown. |
| Gender | Male |
| Race/Ethnicity | Hispanic or Latino |
| Veteran status | **"I am not a Veteran"** (he's a Reservist, separated 2020 — NOT a protected veteran, NO VA disability, NO 180+ active-duty days) |
| Disability | Decline to answer / I do not want to answer |
| Felony | No |
| 18+ | Yes |
| Reliable transportation | Yes |

---

## 5. Auto-skip blockers (do NOT apply; mark skipped + log reason)

- **Location:** onsite or hybrid **outside ~30 miles of Philadelphia 19107**. (Watch for "Remote" postings whose JD says "in-office N days" or names a specific non-Philly metro — e.g. CuraLinc=Chicago 4d, Net at Work=NYC, USCG=Chesapeake VA, MediQuant office.)
- **State-restricted remote:** some remote roles list eligible states. **PA must be in the list** (Encoura excluded PA → skip; ThinkReservations included PA → OK).
- **Federal USA Hire assessment** (~3 hrs) — can't take assessments for him. Skip.
- **Title 32 / National Guard** — ineligible (Reservist, separated 2020). Skip all.
- **Active security clearance required** (not "willing to obtain Public Trust" — that's OK).
- **Specialty cert/skill hard-required:** ODS-C (oncology), UKG/Kronos WFM, Epic, French/Mandarin language, mainframe COBOL/ADABAS, deep web-dev (HTML/CSS/JS for the role itself).
- **Pay below floor:** <$40K equivalent, $500-1000/mo gigs, unpaid, commission-only, 1099-no-benefits, MLM, AI-trainer/data-annotation piecework.
- **Over-senior:** Director/VP/Principal/Staff/Architect, or Manager-of-large-team roles requiring 5+ yrs + leading 7-10 staff + owning ServiceNow migrations (MediQuant, mSupply, ComputerCare/Pinterest, Pepperdine were all real overshoots — skip even though title said "Manager"). Operations agent: a *small-shop* or *coordinator-level* manager is fine; an enterprise multi-team director is not.
- **Same company already submitted** (Lever dedups Magna at email level → "Application already received"; all Magna postings are dead).
- **Account-creation walls you can't pass:** RemoteOK / WeWorkRemotely require account signup before showing the apply form — **go to the company's own careers site instead** (find their "Careers" link → usually SmartRecruiters/Greenhouse/Lever/iCIMS). If the direct site is also dead/empty, skip.

---

## 6. Candidate query (paste-ready; filter by your LANE CATEGORY)

**The lane partition is now a stored `category` column** (added 2026-06-18, `job_pipeline/lane_category.py`), assigned deterministically at summarize time. Each job lands in exactly ONE category, so agents filter `WHERE i.category = '<lane>'` instead of title regexes. **This replaces the SQL SKIP-LOCKED claim approach for PICKING jobs** — that drifted when agents worked off stale lists and caused a real double-apply (Liberty Mutual, 2026-06-18). The dashboard Queue tab has a **Lane** selector (IT Help Desk / IT General / Operations / Remote) with live counts.

Category → agent:
- **Tier 1** → `it_helpdesk` — helpdesk/service desk/desktop/IT support
- **Tier 2** → `it_general` — sysadmin, NOC, network, systems, IT specialist, cyber/infosec, cloud/DBA
- **Operations** → `operations` — ops mgr/coordinator, service mgr, implementation, account mgr, onboarding, CS
- **Remote non-IT** → `remote_non_it` — remote customer support/service, general remote (4th lane, assign later)

```bash
cd "E:/AI Programs/AI-job-application-pipeline" && POSTGRES_PASSWORD=yourpassword POSTGRES_PORT=5433 python -X utf8 -c "
import sys; sys.path.insert(0,'.')
from job_pipeline.db import pg_connect
CATEGORY = 'it_helpdesk'   # <-- your lane: it_helpdesk | it_general | operations | remote_non_it
conn = pg_connect(); cur = conn.cursor()
cur.execute('''
  SELECT p.id, p.company_name, p.title, p.salary_text, p.apply_url, i.list_rank
  FROM job_pipeline_items i JOIN job_postings p ON p.id=i.posting_id
  WHERE i.status='pending_review'
    AND i.category = %s
    AND p.apply_url NOT ILIKE %s
    AND p.company_name NOT IN (
      SELECT DISTINCT p2.company_name FROM job_pipeline_items i2
      JOIN job_postings p2 ON p2.id=i2.posting_id
      WHERE i2.status IN ('submitted','closed') AND p2.company_name IS NOT NULL)
  ORDER BY i.list_rank DESC NULLS LAST LIMIT 15
''', (CATEGORY, '%usajobs%'))
for r in cur.fetchall():
    print(r[0], '|', round(r[5] or 0,3), '|', (r[1] or '')[:28], '|', (r[2] or '')[:50], '|', (r[3] or '')[:16])
    print('   ', r[4])
conn.close()
"
```

> **Backfill / re-tune:** new jobs auto-categorize when summarized. To recategorize existing rows (or after editing `lane_category.py`), call `job_pipeline.db.set_item_category(item_id)` over the queue. Boundary titles resolve to ONE category by precedence (Help Desk → IT General → Operations → Remote), so "IT Operations Support Specialist" goes to **IT General**, not two lanes. If a job looks mis-binned, flag it to Carlos — don't grab another lane's row.

> **Scorer note:** the auto-filter threshold was lowered 0.26 → 0.10 on 2026-06-15 (`job_pipeline_config.json` → `matching.auto_close_combined_below`). Low rank (0.10-0.25) is NOT "unqualified" — it's keyword/title mismatch. Real fits routinely score low (Bartlett 0.16, Conn's 0.14). Read the JD, don't trust the number.

---

## 7. DB status protocol (multi-agent dedup without the AgentWorker)

Three agents share the DB. To avoid two agents grabbing the same row:

- **On pick:** `UPDATE job_pipeline_items SET status='drafting', claimed_by='<agent>', updated_at=NOW() WHERE posting_id=<id>` — this removes it from the other agents' candidate query immediately.
- **On submit:** `UPDATE ... SET status='submitted', updated_at=NOW() WHERE posting_id=<id>`
- **On skip:** `UPDATE ... SET status='skipped', updated_at=NOW() WHERE posting_id=<id>`
- Partitioning by title family (each agent's `TITLE_REGEX`) already makes collisions rare; the `drafting` claim closes the gap.

---

## 8. ATS-specific playbook (quirks learned the hard way)

| ATS (URL hint) | How to drive | Gotchas |
|---|---|---|
| **Lever** (`jobs.lever.co`) | JS-fill via `.application-question`→`.application-label`. Set values with native setter + dispatch input/change. | Screenshots freeze constantly — work blind via JS. Single resume slot (no cover letter); cover PDF stays in Downloads as backup. "Apply for this job" link → `/apply`. |
| **Greenhouse** (`greenhouse.io`, `grnh.se`) | Mostly standard inputs + react-select dropdowns. | Dropdown options need real clicks (isTrusted). Has both resume + cover-letter file slots. |
| **iCIMS** (`icims.com`) | Content is in an iframe: `document.getElementById('icims_content_iframe').contentDocument`. Fields by `[name*="FirstName"]` etc. | Account creation often required + sometimes broken (Bartlett). hCaptcha on signup = Carlos. |
| **ADP Workforce Now** (`workforcenow.adp.com`) | Click Apply (bottom one), fill name/email/phone, Continue. | **Email verification code → Carlos checks Gmail.** Address uses Google autocomplete — click the dropdown suggestion. Multi-step: Personal/Resume/Questions/Voluntary/Submit. |
| **Workday** (`myworkdayjobs.com`) | "Autofill with Resume" → Carlos uploads. Then My Info/Experience/Questions/Voluntary/Self-ID/Review. | **Account creation (password) = Carlos.** Date fields: coordinate-click the MM field then type. Dropdowns are buttons opening listboxes — `find` the option then click. |
| **ClearCompany** (`*.clearcompany.com`, often behind hrmdirect "START YOUR APPLICATION") | Name/email/phone + 2 radios → Continue. | **Email magic-link sign-in → Carlos.** Salary needs strict `$57500.00` numeric format (rejected $55,000-$60,000). Crashed mid-form once — if it does, log deferred + move on. |
| **Indeed SmartApply** (`Apply with Indeed`) | Uses Carlos's on-file Indeed resume/profile; cascades through location/resume/review. | **Auto-submits fast** — sometimes before you can paste a cover letter. Confirm via "Your application has been submitted" URL. |
| **applytojob.com** | Clean single-page form. JS or computer-use. | Has its own resume + cover-letter slots. CAPTCHA on some → Carlos. |
| **Paycor** (`recruitingbypaycor.com`) | Standard multi-field + EEO radios. | Resume + cover-letter upload slots. |
| **freshteam / smartrecruiters / jobvite / dayforce / paylocity** | Standard; read_page to map fields. | Smartrecruiters is where many "direct careers" links land after RemoteOK dead-ends. |

---

## 9. Human gates — STOP and hand to Carlos (with exact filename + URL)

Account creation / passwords · Final Submit click · File uploads (resume + cover letter) · CAPTCHA/reCAPTCHA/hCaptcha · Email/SMS verification codes · Workday isTrusted date/text quirks if JS won't take. At each gate, tell Carlos: the URL, the exact field(s) he must touch, and the exact PDF filename(s) in Downloads to upload.

---

## 10. Honest-framing hard rules (memory-enforced — violating these is the worst failure mode)

- **NEVER "veteran"** framing. "Former Army Reservist" / "I am not a Veteran." (`feedback_not_a_veteran`)
- **NEVER pure "Tier 1"** for BTB — always "Tier 1/Tier 2" or "hands-on IT generalist + small-shop sysadmin." (`feedback_btb_is_tier_1_and_tier_2`)
- **NO HIPAA in Skills tags.** Medical-context discretion can appear in summary/cover-letter prose only, and only when role is medical-adjacent. (`feedback_no_hipaa_on_healthcare_resumes`)
- **NO "Microsoft 365 / M365" in Skills** (implies tenant admin he doesn't do). Office end-user is fine in prose. (`feedback_no_microsoft_365_on_resume`)
- **NO certs-in-progress** on resume (no CompTIA/WIOA/planned exams). WGU enrollment is the only future-credential allowed. Certs can be mentioned in cover-letter gaps paragraph as "pursuing via PA WIOA." (`feedback_no_intent_to_certify_on_resume`)
- **Certs "currently held" on forms:** "None currently active. Pursuing CompTIA A+/Network+/Security+ via PA WIOA training plan." (`feedback_currently_hold_certs`)
- **Resume Projects allowlist:** only Home Cleanliness Assistant + AI Job-Application Pipeline (scoped to aggregation+scoring+tailored-doc-gen; do NOT claim working end-to-end auto-apply). (`feedback_resume_projects_allowlist`)
- **Reason for leaving BTB:** "Position eliminated in operational restructuring" or "Discharge — failure to meet job requirements." NEVER "misconduct" (PA UC determination confirms non-misconduct).
- **Never hand-edit a rendered PDF/YAML after the fact** — fix the YAML and re-render. (`feedback_never_hand_edit_artifacts`)
- **Pipeline tailor / cover-gen / Playwright auto-apply are FORBIDDEN.** Hand-build only. (`feedback_pipeline_aggregation_only`)

---

## 11. Logging

- Session log: `personal_docs/application_log/YYYY-MM-DD_session.md` (one file per day; create if missing). Append per-action: a Submitted-table row on submit, a Skipped-table row on skip. Update `INDEX.md` once per session.
- Then sync the DB status (§7). Log + DB must agree.

---

## 12. Paste-ready startup block (fill in the ONE bracket for your agent)

```
You are the {AGENT} apply agent in Carlos's job-search workflow (one of three:
Tier 1 / Tier 2 / Operations). Read AGENT_APPLY_HANDOFF.md at the project root
FIRST — it has the full workflow, identity facts, form defaults, ATS quirks,
honesty rules, and the candidate query. Auto-loaded memory also applies.

Your scope = the {AGENT} row of the table in §0. Only pick postings whose title
matches your family; partition keeps you off the other two agents' jobs.

Operating rules:
- Hand-build every resume + cover letter as rendercv YAML, render to Downloads.
  The pipeline auto-tailor is FORBIDDEN.
- BEFORE opening any URL: grep the application_log AND check DB status. Carlos's
  memory beats the log.
- Run every JD through the §5 auto-skip blockers before building anything.
- Stop at the §9 human gates and hand Carlos the exact filename + URL.
- On submit: set DB status='submitted' + append to today's session log.
- Honest framing §10 is non-negotiable (no veteran, no HIPAA tag, no M365, Tier 1/2).

Start by running the §6 candidate query with your agent's TITLE_REGEX, show me
the top fits, and wait for me to say which to apply to (or "take the top one").
```

Replace `{AGENT}` with `Tier 1`, `Tier 2`, or `Operations`. Nothing else changes.

---

## 13. HOW to build the resume + cover letter (self-contained — don't rely on memory auto-loading)

This is the consolidated build checklist + worked exemplars. **Read this before writing any YAML.** If memory file `feedback_resume_build_checklist` is present it's the same content; this embed exists so a fresh agent without that memory still builds correctly.

### 13a. Pre-build checklist (scan top to bottom every time)

1. **Date:** cover letter's first line = TODAY (re-check, don't trust yesterday).
2. **Read the full JD.** Extract verbatim: required skills, preferred skills, duties, comp, location/remote, shift/on-call.
3. **Medical-adjacent role?** Yes → may mention "discretion handling sensitive medical and personnel information" in summary + "HIPAA-trained" in the *military bullet prose only*. No → drop HIPAA entirely, use "discretion handling sensitive personnel information."
4. **Header (fixed):** LinkedIn always. GitHub on Tier 1 / Tier 2 (tech); **OMIT on Operations**. Email/phone/location fixed (§3 block).
5. **Titles + dates are FIXED — never alter:** BTB "IT Operations Manager" 2024-09→2026-03; GOT-JUNK "Junior Operations Coordinator" 2019-04→2021-07; Army "U.S. Army Reserve — 338th Medical Brigade / 68W Combat Medic" 2012→2020; WGU B.S. Cybersecurity (start 2026, no end); Rowan B.A. Political Science (3.80 Cum Laude, Pi Sigma Alpha). "Two years" at BTB is the approved framing of 18 months.
6. **BTB tier framing:** NEVER "pure Tier 1." Use "Tier 1/Tier 2 + single-site sysadmin." Surface the Tier 2 proof in bullets: Windows Server (DNS), Local GPO, Cisco VLAN via switch web UI, Veeam backups, malware remediation, runbook authoring, escalation ownership.
7. **Skills section — hard NO list:** no "HIPAA"/"HIPAA-aware" tag; no "Microsoft 365 / M365 / Microsoft Office" tag; no "currently learning/studying"; no "CompTIA A+/Network+/Security+"; no "Certifications: None" section; no table-stakes filler.
8. **Projects (optional section):** only Home Cleanliness Assistant and/or AI Job-Application Pipeline (scoped: aggregation + LLM scoring + tailored-doc gen — NEVER claim working end-to-end auto-apply). Nothing else.
9. **One page. Mandatory.** Render, count pages. Spilling → trim Skills line-items first, then least-relevant BTB bullet, then tighten summary. Never drop GOT-JUNK or Military.
10. **Filenames:** YAML `tailored_<ID>_<ShortTitle>.yaml`; PDFs to Downloads as `Carlos_Roman-Conville_Resume_<Co>.pdf` and `Carlos_Roman-Conville_Cover_Letter_<Co>.pdf`.

### 13b. Cover-letter shape (4 paragraphs, in `cv.sections.cover_letter` as a list)

- **Line 1:** today's date. **Line 2:** "Hiring Team, <Company> — <Dept if known>". **Line 3:** "Dear Hiring Team,".
- **Para 1:** map the JD to BTB; quote their language where it lands ("Your posting says 'the runbooks don't exist yet' — that's exactly what I did at BTB").
- **Para 2:** concrete BTB evidence, bullet-by-bullet against their duty list. "Concretely: (1)… (2)… (3)…"
- **Para 3 — HONEST gaps:** name the specific JD requirements Carlos lacks (Epic, ServiceNow, Okta, Jamf, SOC2, the specific platform) + willingness to ramp. This paragraph is non-negotiable — it's what makes the letter credible.
- **Para 4 — close:** WGU enrollment as continuous-learning signal + mission fit + welcome a conversation. Mention bilingual EN/ES if customer-facing/clinical/civic; mention schedule flexibility if JD wants weekend/evening/on-call.
- **Sign:** `"Sincerely,\nCarlos D. Roman-Conville"`.

### 13c. Complete resume exemplar (Tier 1/Tier 2 — copy and re-tailor)

```yaml
cv:
  name: "Carlos D. Roman-Conville"
  email: "cromanconville@gmail.com"
  phone: "+18563979706"
  location: "Philadelphia, PA"
  social_networks:
    - network: "LinkedIn"
      username: "carlos-roman-conville-068781217"
    - network: "GitHub"                       # delete this entry for Operations
      username: "Carlos-Roman-Conville"
  sections:
    summary:
      - "Hands-on IT generalist with two years of Tier 1/Tier 2 first-and-only IT support at BEAT THE BOMB Philadelphia — first-contact incident triage, accurate ticket documentation, escalation ownership, Windows 10/11 and Windows Server troubleshooting, networking and remote-access support. Two additional years of customer-facing service at 1-800-GOT-JUNK. Remote-ready, bilingual EN/ES; formalizing through WGU's B.S. Cybersecurity."   # <-- retune nouns to the JD
    experience:
      - company: "BEAT THE BOMB"
        position: "IT Operations Manager"
        location: "Philadelphia, PA"
        start_date: "2024-09"
        end_date: "2026-03"
        highlights:
          - "First contact for all IT incidents at the flagship Philadelphia venue — phone, remote-desktop, and walk-up; documented every interaction in a Notion-based ticket workflow built from scratch; escalated to vendors and senior teams per documented procedures."
          - "Tier 2 depth: Windows Server (DNS), Local Group Policy, Cisco VLAN configuration via switch web UI, Veeam backups, malware remediation, hardware install/repair across Windows 10/11 endpoints."
          - "Networking: TCP/IP, DNS, Wi-Fi, basic VPN client support; remote support via TeamViewer and RustDesk."
          - "Authored every helpdesk runbook, SOP, KB article, and training doc for the flagship opening from scratch; trained every new technical hire; covered after-hours events and on-call rotations."   # <-- keep 3-4 bullets, JD-mapped
      - company: "1-800-GOT-JUNK"
        position: "Junior Operations Coordinator"
        location: "NJ/DE Region"
        start_date: "2019-04"
        end_date: "2021-07"
        highlights:
          - "Customer-facing field service: on-site estimates, dispatch coordination, in-person negotiation; job logging in Salesforce (user-level). Promoted twice during tenure."
    military_service:
      - company: "U.S. Army Reserve — 338th Medical Brigade"
        position: "68W Combat Medic"
        start_date: "2012"
        end_date: "2020"
        highlights:
          - "Three years of internal medic instruction — taught drill-weekend classes, developed training content. Discretion handling sensitive personnel information. Honorable discharge."   # medical-adjacent role → may add 'HIPAA-trained' here only
    education:
      - institution: "Western Governors University"
        area: "Cybersecurity and Information Assurance"
        degree: "B.S."
        location: "Online"
        start_date: "2026"
      - institution: "Rowan University"
        area: "Political Science"
        degree: "B.A."
        location: "Glassboro, NJ"
        start_date: "2016-09"
        end_date: "2019-05"
        highlights:
          - "Final GPA 3.80, Cum Laude. Pi Sigma Alpha (National Political Science Honor Society) inductee."
    skills:
      - label: "Help Desk and Support"            # <-- relabel/retune these 3 groups per JD
        details: "First-contact triage, ticket documentation, escalation routing, SLA awareness, remote support (TeamViewer, RustDesk), customer-facing communication"
      - label: "Endpoints and Servers"
        details: "Windows 10/11, Windows Server (DNS), Local GPO, hardware install/repair, peripherals, Veeam backups, malware remediation"
      - label: "Networking and Tools"
        details: "TCP/IP, DNS, Cisco VLAN via switch web UI, basic VPN, Notion ticketing, PowerShell, Python, SQL"
design:
  theme: "classic"
```

**Operations variant of the exemplar:** delete the GitHub `social_networks` entry; rewrite the summary to lead "IT Operations Manager (BTB) + Junior Operations Coordinator (GOT-JUNK) = ~4.5 years of operations experience — team coordination, vendor management, SOP/runbook authoring, training program build, scheduling, incident/escalation ownership"; rewrite BTB highlights toward people/process/vendor/reporting (not VLAN/GPO); relabel skills groups to "Operations & Coordination / Process & Documentation / Tools & Systems."

### 13d. Cover-letter exemplar (copy and re-tailor)

```yaml
cv:
  name: "Carlos D. Roman-Conville"
  location: "Philadelphia, PA"
  email: "cromanconville@gmail.com"
  phone: "+18563979706"
  sections:
    cover_letter:
      - "June 17, 2026"                         # <-- TODAY
      - "Hiring Team, <Company> — <Dept>"
      - "Dear Hiring Team,"
      - "Your <exact title> posting maps directly onto the last two years of my work at BEAT THE BOMB Philadelphia: <2-3 JD duties echoed back>. <one line tying their framing/mission to his experience>."
      - "Concretely: (1) <BTB evidence for JD duty 1 with specific tools>; (2) <duty 2>; (3) <duty 3>. BTB Philadelphia was a flagship opening, so I authored every helpdesk runbook, SOP, and KB article for the location from scratch."
      - "I want to be honest about the gaps: I do not have direct <named JD requirement(s) he lacks> experience, and I would come in ready to learn them. I do not currently hold A+/Network+/Security+ — I am pursuing the CompTIA stack via the PA WIOA training plan. What I have is real two-year hands-on Tier 1/Tier 2 support experience, strong documentation discipline, and <one more honest strength>. I am bilingual English/Spanish."
      - "I am enrolled in WGU's B.S. in Cybersecurity and Information Assurance, which formalizes the operational foundation I built at BEAT THE BOMB. <company>'s <mission/culture hook> fits how I want to grow. I would welcome the chance to talk further."
      - "Sincerely,\nCarlos D. Roman-Conville"
design:
  theme: "classic"
```

### 13e. Best existing files to copy from (in `generated_resumes/`)

- **Tier 1:** `tailored_9933_BHGriner_Help_Desk_Technician.yaml`, `tailored_9924_CoreBTS_Service_Desk_1st_Shift.yaml`, `tailored_9906_SurfBar_IT_Help_Desk_Technician.yaml`
- **Tier 2 / NOC / sysadmin-lean:** `tailored_10049_BlackHawk_NOC_Technician.yaml`, `tailored_10057_Logicalis_System_Operator_Night.yaml`, `tailored_9936_DMI_Tier_I_Technician.yaml`
- **Operations:** `tailored_10079_ARMStrong_Healthcare_Recovery_Ops_Manager.yaml`, `tailored_9907_FuturePlans_Field_Support_Training_Mgr.yaml`, `tailored_10055_CW_MSP_Service_Coordinator.yaml`

Each `<file>.yaml` has a matching `<file>_cover_letter.yaml`. Copy the closest one, change the company/title/summary/highlights/skills to match the new JD, keep the fixed facts (§3) verbatim, render, confirm one page.

### 13f. Render command (per file)

```powershell
Set-Location "E:\AI Programs\AI-job-application-pipeline\generated_resumes"
rendercv render tailored_<ID>_<Title>.yaml --pdf-path "$env:USERPROFILE\Downloads\Carlos_Roman-Conville_Resume_<Co>.pdf"
rendercv render tailored_<ID>_<Title>_cover_letter.yaml --pdf-path "$env:USERPROFILE\Downloads\Carlos_Roman-Conville_Cover_Letter_<Co>.pdf"
```

### 13g. Pre-submit sanity sweep

Date = today · all titles/dates correct · "Honorable discharge" once · no "veteran" word anywhere · LinkedIn renders (GitHub too unless Operations) · no M365/HIPAA in Skills · no CompTIA-pursuit on resume · **one page** · cover-letter para 3 has a real gap disclosure · at least one bullet per major JD duty.
