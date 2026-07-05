# Multi-Agent Auto-Apply Runbook

> ⚠️ **PARTIALLY SUPERSEDED (2026-06-17).** The `AgentWorker.build_package()` /
> resume-tailor / cover-letter-generator path described below is **FORBIDDEN** —
> Carlos rejected the auto-tailor. For the current hand-built apply workflow +
> per-agent (Tier 1 / Tier 2 / Operations) scope, read **`AGENT_APPLY_HANDOFF.md`**
> at the project root. The queue mechanics here (SKIP LOCKED, claim columns,
> reaper, status protocol) are still valid for dedup; the automated build steps
> are not.

**Status:** v1 — queue is smoke-tested; worker pattern is not yet battle-tested. Report breakage in [memory/MEMORY.md](C:\Users\rexoc\.claude\projects\E--AI-Programs-AI-job-application-pipeline\memory\MEMORY.md) so future sessions inherit fixes.
**Last updated:** 2026-06-03

This document is the canonical reference for running multiple Claude Code CLI sessions in parallel against the shared job-pipeline DB. Paste sections directly into new sessions — every code/prompt block in this file is paste-ready.

---

## TL;DR

- **The code is identical for every agent.** Only two things change per session: `AGENT_ID` and `ATS_FILTER`.
- **Queue is Postgres SKIP LOCKED** (`job_pipeline/db.py`). No Redis, no RabbitMQ.
- **Operating model is interactive, not autonomous.** Each agent acts on Carlos's prompt one job at a time — the same way Carlos drives a single Claude Code session today. The queue's job is to prevent double-grabs across parallel agents, NOT to autoloop unattended.
- **3 agents = 3 conversations Carlos can switch between.** While Carlos resolves Agent A's Workday text-typing snag, Agent B has already prepped its Lever app and is waiting for the Submit click. Carlos serves agents in whatever order they need him.
- **Don't run more than 3-5 parallel sessions.** Three ceilings converge: Anthropic Max-plan rolling budget (shared across sessions), browser-process RAM (~500MB-1GB per Chrome context), and Carlos-on-the-keyboard for human gates.
- **Sonnet 4.6 on parallel sessions.** Opus 4.7 only on the one session driving the hardest ATS (probably Workday).
- **Each session needs its own Chrome browser profile** (`--user-data-dir=%TEMP%\claude-{AGENT_ID}`) or Playwright MCP refuses to launch concurrent sessions.

## The actual operating model (how Carlos drives agents)

```
Carlos:        "apply for next job"
Agent A:       claims row, builds package, opens Chrome, fills form...
               hits Workday isTrusted gate
               pings Carlos: "type these 5 fields by hand"
Carlos:        types fields, "done"
Agent A:       continues, fills more, reaches Submit
               pings Carlos: "ready to submit, here's the URL"
Carlos:        reviews, clicks Submit, "done, log it"
Agent A:       worker.mark_submitted(...), goes idle
Carlos:        (later) "apply for next job"
Agent A:       claims next row, repeats
```

While Carlos is helping Agent A through its Workday gates, Agents B and C are already mid-application on their own ATSes — each waiting on a different human gate. Carlos picks whichever agent pings him next. No agent auto-loops in the background; each waits for Carlos's next instruction.

**Heartbeating matters.** If Carlos takes 20+ minutes on one agent's snag, the 15-minute lease on the other two agents' claims would expire and the reaper would release their rows. So every agent calls `worker.heartbeat(item["id"])` every ~5 minutes while waiting on Carlos. The runbook's example loop shows this.

---

## 1. Per-agent configuration table

Pick rows from this table when spinning up new sessions. **Never run two sessions with the same `AGENT_ID`** — they'll fight for the same claims and the second-to-act will lose ownership checks.

| AGENT_ID | ATS_FILTER (SQL ILIKE) | Suggested model | Notes |
|---|---|---|---|
| `auto-apply-greenhouse-1` | `%greenhouse%` | Opus 4.7 | Clean ATS, react-select dropdowns need manual click (Carlos already knows the pattern) |
| `auto-apply-lever-1` | `%lever.co%` | Opus 4.7 | Cleanest ATS in the mix. Stale validation can hold — re-fill any unrelated field to refresh React state |
| `auto-apply-rippling-1` | `%ats.rippling.com%` | Opus 4.7 | Phone field sometimes drops during batch fill — verify before submit |
| `auto-apply-workable-1` | `%workable%` | Opus 4.7 | URL ends `?success` on submission — easy to confirm |
| `auto-apply-workday-1` | `%myworkdayjobs%` | Opus 4.7 | Hardest ATS: account creation, isTrusted text reducer, multi-step forms. Highest reasoning load. |
| `auto-apply-taleo-1` | `%taleo.net%` | Opus 4.7 | Federal/legacy. Multi-step. Account often pre-filled from prior application. |
| `auto-apply-applytojob-1` | `%applytojob%` | Opus 4.7 | Inline expanding forms; fewer human gates |

**Recommended starting set: 3 sessions max.** A typical solid configuration is Workday + Lever + Greenhouse (or substitute whichever has claimable rows after ingestion).

### Model choice — why Opus on all auto-apply sessions

Auto-apply work is **judgment-heavy**: reading JD text to write honest-framing answers, recognizing subtle ATS-field intent ("currently hold" vs "have completed training"), knowing when to ping Carlos vs fill autonomously, recovering from form-validation errors, detecting fit-gaps mid-flow. Opus 4.7 is meaningfully better than Sonnet 4.6 across all of these. Per-turn cost is ~5x but per-application cost is closer to 2-3x because Opus uses fewer turns and produces cleaner first-shot answers.

**Where the budget savings should actually come from:** background Claude Code sessions you have open but aren't actively driving — Lora tests, Spanish practice, file organization research, Lore writing, OSRS leveling, etc. Those don't need Opus's reasoning depth. Drop them to Sonnet 4.6 via `/model` and drop "Extra" reasoning. That's where you reclaim the rolling-window headroom.

**If you start hitting "API limit reached" mid-day on Max 20x with all 3 auto-apply on Opus:** the fastest fix is closing or pausing background sessions, NOT downgrading auto-apply. The crash itself is the signal — until you hit it, stay on Opus.

---

## 2. The paste-able session startup prompt

Copy this entire block into a fresh Claude Code session in the project root. Replace `{AGENT_ID}` and `{ATS_FILTER}` with values from the table above. Everything else stays verbatim.

```
You are an auto-apply worker in a multi-session Claude Code setup against
Carlos's job-application pipeline. The MULTI_AGENT_APPLY_RUNBOOK.md at the
project root explains the full system. Before any work, read it. Auto-loaded
memory files relevant to this role: reference_multi_agent_runbook,
reference_skip_locked_queue, reference_application_log, feedback_targeting,
feedback_currently_hold_certs, feedback_resume_projects_allowlist.

Your configuration:
  AGENT_ID   = "{AGENT_ID}"          # e.g. "auto-apply-greenhouse-1"
  ATS_FILTER = "{ATS_FILTER}"        # e.g. "%greenhouse%"

Hard constraints:
  - Tier 1 / L1 help desk only. No Tier 2, senior, lead, engineer, architect.
  - Honest framing: do NOT overclaim certs, projects, or skills. The resume
    tailor enforces this server-side; you must also enforce it when answering
    ATS application-form free-text questions in your own words.
  - You MAY NOT: enter passwords, create accounts, click final Submit, upload
    files, solve CAPTCHAs. At each of these gates, ping Carlos with the exact
    fields he needs to touch and the URL.
  - Switch model to Sonnet 4.6 unless the runbook table assigns this session
    Opus 4.7 (run /model). Drop /thinking Extra unless this is Opus-Workday.

Step 1 - preflight (do this BEFORE anything else):
  Run from a terminal in the project root:
      python scripts/agent_preflight.py {AGENT_ID} '{ATS_FILTER}'
  The script verifies DB connectivity, schema migrations, Downloads dir
  writability, service module importability, and reports how many rows are
  currently claimable. If it exits non-zero, FIX the listed errors before
  continuing - do NOT start claiming.

Step 2 - instantiate the worker ONCE at session start:
  from job_pipeline.agent_worker import AgentWorker

  worker = AgentWorker(agent_id="{AGENT_ID}", ats_filter="{ATS_FILTER}")
  ok, errors = worker.preflight()
  assert ok, errors

  Then WAIT for Carlos's first "apply for next job" instruction. Do NOT
  start claiming on your own. You are an interactive agent, not a daemon.

Step 3 - on each "apply for next job" prompt from Carlos:

  # a) Claim ONE row.
  item = worker.claim_next()
  if item is None:
      # Tell Carlos: "No claimable rows for {ATS_FILTER} right now.
      # Ingestion may be stale or this slice is drained. Want me to wait,
      # broaden the filter, or pause this agent?"
      # Do NOT auto-poll. Wait for Carlos's next instruction.

  # b) Build the tailored package.
  pkg = worker.build_package(item["id"], mode="cover_letter_only")
  if not pkg.get("ok"):
      worker.mark_failed(item["id"], reason=f"build: {pkg.get('error')}")
      # Tell Carlos what went wrong; ask whether to retry or move on.

  # c) Stage the PDFs in Downloads with this agent's per-agent name.
  cover_pdf = worker.stage_for_upload(pkg.get("cover_pdf"), kind="cover_letter")
  # resume_pdf = worker.stage_for_upload(pkg.get("resume_pdf"), kind="resume")

  # d) Drive Chrome MCP. CRITICAL: spawn your tab group fresh; do not touch
  #    tabs belonging to other agents. When you hit a human gate, MESSAGE
  #    Carlos with:
  #      - The exact URL
  #      - The exact field labels he must touch (e.g. "First/Last/Address
  #        - Workday isTrusted rejects my JS typing; use space+backspace")
  #      - The exact cover-letter filename to upload from Downloads:
  #            <cover_pdf.name>
  #        (per-agent so it won't collide with parallel sessions)
  #    Then heartbeat every ~5 min while waiting:
  #            worker.heartbeat(item["id"])
  #    If heartbeat returns False, the row was reaped. Stop work; tell
  #    Carlos the lease expired and ask what to do.
  #
  #    Browser tab discipline: at session start call tabs_create_mcp() ONCE
  #    and remember the returned tabId. Pass tabId=<that value> on EVERY
  #    browser-tool call. Never act on tabs belonging to other agents.

  # e) When Carlos confirms "done, log it" (after he clicks Submit):
  worker.mark_submitted(item, confirmation_url="<the URL>")
  # Go idle. Wait for Carlos's next "apply for next job" prompt.

  # On unrecoverable browser-side failure (404, login wall, ADP account-
  # create gate):
  # worker.mark_failed(item["id"], reason="account-gate / 404 / expired")
  # Tell Carlos what failed; he'll decide whether to move on.

Honest framing in ATS form answers:
  - "Certifications currently held?" -> "None currently active. Pursuing
    CompTIA A+, Network+, Security+ via PA WIOA training plan (intake in
    progress)." (HIPAA training is past, not currently held.)
  - "Veteran status?" -> Yes veteran. NOT protected-veteran unless confirmed
    (no current VA disability; no 180+ active-duty days; no campaign-badge /
    wartime / recently-separated category match).
  - "Reason for leaving BEAT THE BOMB?" -> "Terminated" or "Discharge -
    failure to meet job requirements / standards." Never "misconduct." The PA
    UC Notice of Determination in personal_docs/dvop_intake confirms
    non-misconduct.

Never reapply: the claim WHERE-clause already filters submitted rows out of
the candidate pool. Before clicking Submit, double-check by reading the row's
current status. If it has somehow become 'submitted', call mark_submitted
with notes="dup-prevented" and skip.

Begin with Step 1 preflight.
```

---

## 3. What stays the same across every session (the actual code)

The Python is **identical** for every agent. Only the two string values change. **Use the `AgentWorker` class** — it wraps claim/build/stage/heartbeat/release/log into a coherent API and solves the Downloads-filename-collision problem (three agents would otherwise overwrite each other's cover-letter PDFs before Carlos uploads them).

The pattern is **interactive one-shot, not autonomous loop**. Each "apply for next job" instruction from Carlos triggers exactly ONE pass through claim → build → drive → submit. The agent does not loop on its own. After release, the agent waits idle for Carlos's next prompt.

```python
from job_pipeline.agent_worker import AgentWorker

# === PER-SESSION CONFIG (set once at session start) ======================
AGENT_ID   = "auto-apply-greenhouse-1"
ATS_FILTER = "%greenhouse%"
# ==========================================================================

# Session-start: instantiate the worker once and run preflight.
worker = AgentWorker(agent_id=AGENT_ID, ats_filter=ATS_FILTER)
ok, errors = worker.preflight()
if not ok:
    raise SystemExit("preflight failed: " + "; ".join(errors))

# ---- ONE PASS PER CARLOS PROMPT --------------------------------------------
# Called when Carlos says "apply for next job" / "next one" / similar.

# 1. Claim the next eligible row for this agent's ATS slice.
item = worker.claim_next()
if item is None:
    # Tell Carlos: "No claimable rows for {ATS_FILTER} right now.
    # Either ingestion is stale or this ATS slice is drained."
    # Do NOT auto-poll - just report and wait for next instruction.
    return

# 2. Build the tailored package (svc_decide + svc_build_package).
pkg = worker.build_package(item["id"], mode="cover_letter_only")
if not pkg.get("ok"):
    worker.mark_failed(item["id"], reason=f"build: {pkg.get('error')}")
    # Tell Carlos the build failed and what was claimed; offer to try again.
    return

# 3. Stage PDFs in Downloads with this agent's per-agent filename.
cover_pdf = worker.stage_for_upload(pkg.get("cover_pdf"), kind="cover_letter")
# resume_pdf = worker.stage_for_upload(pkg.get("resume_pdf"), kind="resume")  # if tailored

# 4. Drive Chrome MCP. When you hit a human gate, message Carlos with:
#    - The exact URL
#    - The exact field labels Carlos must touch
#    - The exact cover-letter filename to drop: cover_pdf.name
#      (the filename is per-agent: Carlos_Roman-Conville_Cover_Letter_{AGENT_ID}.pdf)
#    Then HEARTBEAT every ~5 minutes while you wait:
#         worker.heartbeat(item["id"])
#    Carlos's review can take 10-20 minutes per Workday-class form;
#    without heartbeating, the 15-min lease would expire and the reaper
#    would release this row to another agent mid-flight.

# 5. Carlos says "done, log it" after he submits. Then:
worker.mark_submitted(item, confirmation_url="<the confirmation URL>")
# Agent goes idle here. Wait for Carlos's next "apply for next job" prompt.

# Or if the application died irrecoverably (404, login wall, ADP, account gate):
# worker.mark_failed(item["id"], reason="account-gate / 404 / expired listing")
```

**Heartbeating is the one thing the agent must do unprompted.** Everything else is driven by Carlos's messages. While waiting on Carlos at a human gate, the agent should call `worker.heartbeat(item["id"])` every ~5 minutes. This is the only background behavior — no polling, no auto-claim, no auto-submit.

### Optional: autonomous draft-only batch mode

If Carlos later wants to draft 30 packages overnight unattended (no submissions, just packages ready in `package_ready` status for morning batch-submit), the same `AgentWorker` can be looped — but that's a different operational mode. Not the default. See "Open work" section for the overnight-draft pattern.

### Optional: lower-level direct API

If you need finer control (e.g. claiming with a custom WHERE clause), you can use the underlying helpers in `job_pipeline.db`: `claim_next_item`, `heartbeat_claim`, `release_claim`, `reap_stale_claims`, `list_active_claims`. The `AgentWorker` is a convenience layer over these.

---

## 4. ATS partitioning — OPTIONAL, not required

**Default: don't partition.** SKIP LOCKED already prevents double-grabs across all agents, so partitioning by ATS family is NOT a correctness requirement. The simpler and usually better default is: every agent pulls from the same pool sorted by fit score (no `ats_filter` set on the `AgentWorker`). Each agent naturally gets a different row because the DB locks the row at claim time.

**Cost of partitioning if you do it:** if Agent 2 is pinned to `%lever%` and the Lever pool is drained, Agent 2 sits idle while Workday or Indeed rows are available. Artificial throughput cap for no correctness gain.

**When partitioning IS worth it:**
- **You want one specific agent on a particular ATS family.** For instance, you might want your Opus-attended session focused on Workday-class hard ATSes while the others handle whatever's easier.
- **You're worried about same-employer parallel applications.** Two agents applying to two different jobs at the same Workday tenant simultaneously could fight over cookies/modals in the shared Chrome. In practice with three agents and fit-score-ordered claiming, they'll naturally be on different employers, so this rarely happens.

If you want a session to specialize: pass an `ats_filter` like `"%myworkdayjobs%"` when instantiating `AgentWorker`. If you want it to pull from the whole pool: pass nothing (or empty string, or `"%"`).

---

## 5. Human gates — when each session MUST stop and ping you

Per safety rules + ATS reality, every session must hand off at these points:

| Gate | Why automated agents can't |
|---|---|
| Account creation (passwords) | Prohibited |
| Final Submit click | Always Carlos for safety |
| File uploads (resume / cover letter) | `file_upload` only accepts session-attached files; Carlos drag-drops |
| CAPTCHA / reCAPTCHA | Not solvable autonomously |
| Workday `isTrusted` text fields | React reducer rejects JS-typed values; needs Carlos's space+backspace trick |
| Greenhouse react-select option clicks | Same `isTrusted` check; Carlos must click the dropdown options |
| OAuth / SSO consent flows | Permission gate |
| Indeed Apply / iCIMS / ADP account login walls | Account-gated |

When a session hits one, the pattern is: pause, ping Carlos with the exact field list + URL, call `heartbeat_claim` on a timer while waiting, resume on Carlos's "go."

---

## 6. Costs, rate limits, and parallel-session math

You're on Claude.ai **Max 20x (~$200/mo)**. Important facts:

- All parallel sessions under one Anthropic account **share a single rolling budget**, not per-session quotas
- Opus 4.7 burns the budget ~**5x** faster than Sonnet 4.6 per equivalent turn
- "Extra" reasoning adds output tokens on top of base
- Batch API (50% off) is **NOT available** on Max subscription — requires API-key billing

**Practical math:** at 3 Opus 4.7 sessions running auto-apply concurrently, on Max 20x with background sessions all on Sonnet 4.6 (or closed), you should comfortably stay under the rolling cap for a full working day of applications. Opus per-application cost is roughly 2-3x Sonnet (per-turn is ~5x but Opus uses fewer turns) — but the judgment-quality on form-filling and free-text answers is worth the spend.

**The single biggest budget lever is closing or downgrading background sessions you're not actively driving.** Lora tests, Spanish practice, Lore writing, OSRS leveling, profile improvement, folder organizer — drop those to Sonnet 4.6 with `/model` and drop "Extra" reasoning. That's where the rolling-window headroom for auto-apply Opus comes from.

If you do hit "API limit reached" mid-day with all 3 auto-apply on Opus, the right reaction is:
1. Close any background sessions you haven't touched in 5+ minutes
2. Switch any remaining background sessions to Sonnet 4.6
3. Wait for the 5-hour rolling window to slide
4. **Do NOT downgrade the auto-apply sessions** — they're where the budget is best spent

---

## 7. Browser isolation — tab groups, not profiles

Carlos uses **Claude in Chrome** (the MCP browser extension), not standalone Playwright. This means:

- **All Claude Code sessions share the same Chrome browser instance.** No "--user-data-dir per agent" trick is needed — there's only one Chrome and one extension.
- **Cookies, logged-in sessions, autofill state are shared.** When Agent A signs into Workday tenant X, Agents B and C inherit that login. Usually a feature (no duplicate logins) but agents must not stomp on each other's flows.
- **Each agent gets its own tab group** via `tabs_create_mcp`. Tab groups isolate which tabs each agent can read or click. Agent A must NEVER act on a tab in Agent B's group.

### Per-agent tab-group discipline

At session start, every agent calls `tabs_create_mcp` to claim a fresh tab group, then passes that `tabId` explicitly on every subsequent browser-tool call. If an agent calls a browser tool without a `tabId`, the MCP defaults to the agent's first tab — but that breaks fast as soon as multiple agents are active.

Pattern (every agent runs this once at session start):

```
# Inside Claude Code, the agent invokes the tool:
tabs_create_mcp()
# This returns a new tabGroup + tabId. Remember the tabId for the whole
# session. Pass tabId=<that value> on every navigate/click/fill/screenshot.
```

If `tabs_context_mcp` shows tabs that belong to other agents, ignore them. Only act on the tab you created.

### Account / cookie hazards in a shared browser

Because the browser is shared:

- **Don't log into the same employer's ATS from two agents at once.** If Agent A is mid-flow on Workday tenant X and Agent B navigates to the same tenant's careers page, they'll fight over modals / cookies.
- **ATS partitioning helps.** Agent A drives Workday postings; Agent B drives Lever; Agent C drives Greenhouse. They don't overlap, so they don't fight.
- **For Workday specifically:** Carlos has different accounts on different tenants (Vanguard, ABCBS, Volaris/AssetWorks). Each agent should stick to its assigned tenant for the session.

---

## 8. Smoke tests — verify the queue + worker still work

Two smoke tests exist. **Run both before spinning up parallel sessions for the first time, and again after any schema or queue-helper change.**

```
# Queue primitive (claim/heartbeat/release/reaper + ownership checks)
POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=yourpassword \
  python scripts/smoke_skip_locked_queue.py

# AgentWorker end-to-end (preflight, claim, stage, log, submit, fail paths)
POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=yourpassword \
  python scripts/smoke_agent_worker.py
```

Both must end with `ALL TESTS PASSED`. Each restores the rows / log files it touched.

### Reaper daemon (run in its own terminal)

The reaper releases stale claims so crashed agents don't strand rows. It's REQUIRED for self-healing in a multi-agent setup. Start it in a separate terminal at the beginning of your work session and leave it running:

```
POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=yourpassword \
  python scripts/reaper_daemon.py
```

It reaps every 60 seconds and prints what it freed. Ctrl-C to stop. Safe to run multiple instances concurrently — the UPDATE is atomic.

### Per-agent preflight

Before starting a new agent session's work loop:

```
python scripts/agent_preflight.py auto-apply-greenhouse-1 '%greenhouse%'
```

Exit code 0 = ready, 1 = errors listed. Also reports how many rows are currently claimable for your filter and snapshots active claims across all agents.

---

## 9. Observability — see what's running

From any session:

```python
from job_pipeline.db import list_active_claims
for c in list_active_claims():
    print(c)
```

Returns every row currently held by any agent, with `lease_expired` flag for stale claims that the reaper hasn't gotten to yet. Useful for "who's working on what right now" snapshots.

To run the reaper manually (releases stale claims):

```python
from job_pipeline.db import reap_stale_claims
for item_id, prior_status, reverted_to in reap_stale_claims():
    print(f"reaped {item_id}: {prior_status} -> {reverted_to}")
```

---

## 10. Schema reference

Three columns on `job_pipeline_items` (added 2026-06-03):

| Column | Type | Purpose |
|---|---|---|
| `claimed_by` | `TEXT` | The `AGENT_ID` string that currently owns this row |
| `claimed_at` | `TIMESTAMPTZ` | When the claim was taken |
| `lease_expires_at` | `TIMESTAMPTZ` | Reaper releases the row when this is in the past |

Plus a partial index `idx_job_pipeline_items_claim_lookup` on `(status, lease_expires_at) WHERE claimed_by IS NOT NULL` to keep claim lookups cheap.

Default lease is **15 minutes** (`DEFAULT_CLAIM_LEASE_MINUTES`). Reaper revert map (`_REAPABLE_STATUS_TO_REVERT` in `db.py`):
- `drafting` → `ranked`
- `tailoring` → `ranked`

If you add a new in-progress status (e.g. `submitting` when you wire browser auto-apply), extend that dict.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `claim_next_item` always returns `None` | No rows in `ranked` status matching your ATS filter | Run `SELECT count(*) FROM job_pipeline_items WHERE status='ranked'` to confirm pool; widen filter or sync your ingestion |
| `heartbeat_claim` returns `False` | Lease already expired (reaper got there first), OR a different agent stole the claim | Stop work on that row; it's no longer yours. Loop and claim the next one |
| `release_claim` returns `False` | Ownership check failed — wrong `AGENT_ID` or already released by reaper | Verify the `AGENT_ID` string matches the one used for `claim_next_item`. If reaper already released, pass `require_ownership=False` only if you're certain you should override |
| Browser tool acts on wrong tab (e.g. another agent's Workday flow) | Agent forgot to call `tabs_create_mcp` at session start, OR forgot to pass `tabId=<own>` on a tool call | Always call `tabs_create_mcp` once at session start, remember the tabId, pass it on every browser-tool invocation |
| Two agents fight over the same login modal | Both agents drove to the same employer's ATS in the shared Chrome | ATS partition agents by family (Workday / Greenhouse / Lever) so they don't overlap. If you need two agents on the same ATS family, sequence them — don't run concurrently on the same tenant |
| Multiple sessions hit `API limit reached` simultaneously | Max-plan 5-hour rolling window saturated | Switch background sessions (non-auto-apply) to Sonnet 4.6 and drop "Extra"; reduce parallel auto-apply count |
| Smoke test fails on `[test 1]` | SKIP LOCKED query broken or migration not applied | Re-run `init_job_pipeline_schema()` from Python; inspect the schema with `\d job_pipeline_items` in psql |

---

## 12. What's done and what's NOT done

**Done (2026-06-03):**

- ✅ Postgres SKIP LOCKED queue + lease/heartbeat/reaper primitives (`job_pipeline/db.py`)
- ✅ Schema migration (3 columns + partial index)
- ✅ Queue smoke test (`scripts/smoke_skip_locked_queue.py`)
- ✅ `AgentWorker` class (`job_pipeline/agent_worker.py`) — encapsulates the loop
- ✅ Per-agent Downloads filename to prevent cover-letter collisions
- ✅ Reaper daemon (`scripts/reaper_daemon.py`)
- ✅ Per-agent preflight (`scripts/agent_preflight.py`)
- ✅ AgentWorker end-to-end smoke test (`scripts/smoke_agent_worker.py`)
- ✅ Application-log auto-write on submission

**Still NOT done:**

- **Per-agent dashboard view.** `list_active_claims()` exists but isn't surfaced in the Streamlit dashboard yet.
- **Submission-step claim.** Currently the same agent that claims `ranked → drafting` also drives the browser through `package_ready` to `submitted`. If you want to split the work (one agent drafts, a different agent submits), add a `submitting` claim with its own reaper entry.
- **Workday `isTrusted` typing automation.** The single highest-leverage gap — would unblock parallel Workday sessions. Likely path: CDP `Input.dispatchKeyEvent` from a Playwright session. Not in scope.
- **Auto-loading the API key into worker calls.** Today the worker calls `svc_*` Python functions directly (bypassing the API server's auth). If you want to switch to HTTP later, you'll need to inject the N8N_API_KEY into requests.

When you tackle any of these, update the relevant memory file + this runbook.

---

## See also

- [`memory/reference_skip_locked_queue.md`](C:\Users\rexoc\.claude\projects\E--AI-Programs-AI-job-application-pipeline\memory\reference_skip_locked_queue.md) — queue API quick reference (auto-loaded in every CC session)
- [`memory/reference_application_log.md`](C:\Users\rexoc\.claude\projects\E--AI-Programs-AI-job-application-pipeline\memory\reference_application_log.md) — application-log convention (every submission goes here)
- [`memory/feedback_targeting.md`](C:\Users\rexoc\.claude\projects\E--AI-Programs-AI-job-application-pipeline\memory\feedback_targeting.md) — job-search scope
- [`memory/feedback_currently_hold_certs.md`](C:\Users\rexoc\.claude\projects\E--AI-Programs-AI-job-application-pipeline\memory\feedback_currently_hold_certs.md) — honest cert framing
- [`memory/feedback_resume_projects_allowlist.md`](C:\Users\rexoc\.claude\projects\E--AI-Programs-AI-job-application-pipeline\memory\feedback_resume_projects_allowlist.md) — which personal projects may appear on resume / cover letter
- [`job_pipeline/db.py`](E:\AI Programs\AI-job-application-pipeline\job_pipeline\db.py) — source of truth for the queue helpers
- [`scripts/smoke_skip_locked_queue.py`](E:\AI Programs\AI-job-application-pipeline\scripts\smoke_skip_locked_queue.py) — lifecycle test
- Deep-research report `wf_53d4da41-92b` (2026-06-03 session transcript) — full architecture analysis behind this design
