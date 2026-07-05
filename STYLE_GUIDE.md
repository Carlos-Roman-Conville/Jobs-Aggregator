# Resume + Cover-Letter Style Guide

This is the **human** source of truth for how a tailored resume and cover letter
should be structured, worded, punctuated, and cased. The **machine** source of
truth is [`job_pipeline/style_rules.yaml`](job_pipeline/style_rules.yaml), enforced
by [`job_pipeline/presentation_linter.py`](job_pipeline/presentation_linter.py).
Keep the two in sync: when you add a rule here, add it to the YAML (or vice-versa).

## The core idea: three enforcement layers

Every quality problem falls into one of three buckets, and each bucket is enforced
by a *different mechanism*. Putting a rule in the wrong layer is why "obvious"
things used to slip through.

| Layer | What it handles | Mechanism | Consistency |
|---|---|---|---|
| **Deterministic** | Objective, rule-shaped defects — casing, banned phrases, punctuation, bullet structure, generic summary openers | `presentation_linter.py` reading `style_rules.yaml` | Fires **100% of the time**, identically every run |
| **Judgment** | Subjective quality — tone, persuasiveness, relevance, credibility | LLM critique loop (`critique_loop.py`) + a calibrated judge | Variable run-to-run; that's expected |
| **Truth** | Whether a claim is *true* | `truth_classifier.py`, `evidence_db.py`, `named_requirements.py`, `light_exposure` | Always wins over everything else |

**Golden rule:** if a defect can be described as a fixed rule, it belongs in the
deterministic layer — never leave it to the LLM. The LLM is for things only
judgment can assess. The truth layer outranks both: the presentation linter may
**reword** a claim but must **never invent or inflate** one.

### Severity model

- **autofix** — the linter rewrites it deterministically and logs a note. No human action.
- **warn** — the linter flags it and feeds a penalty into the score; the text is left as-is for the LLM/human to address.
- **block** — the field is considered unexportable and should be regenerated.

---

## 1. Capitalization & casing

Inconsistent capitalization (`Windows OS troubleshooting` next to `windows os
troubleshooting`) is the single most common "tells it's sloppy" defect. The fix is
a **canonical casing map**: one spelling, one casing, everywhere.

- **Skills** use Title Case, except acronyms which stay uppercase. `dns` → `DNS`,
  `m365` → `Microsoft 365`, `help desk` → `Help Desk`. *(autofix)*
- **Acronyms** always uppercase: IT, PC, OS, DNS, VPN, SSO, MFA, ITSM, SOP, TCP/IP,
  PST, AD, API, SQL. Never `Dns` or `Sso`. *(autofix)*
- **Proper nouns in prose** are re-cased only for an unambiguous allow-list
  (Windows, PowerShell, macOS, Active Directory, Microsoft 365, SharePoint,
  Salesforce). Ambiguous words like *Word*, *Excel*, *Teams* are **left alone** to
  avoid mis-casing common-noun uses. *(autofix)*
- **No accidental ALL CAPS** runs (more than 3 consecutive all-caps words that
  aren't acronyms). *(warn)*

> Wrong: `Skills: windows os troubleshooting, help desk, ITSM, ticketing/itsm, dns`
> Right: `Skills: Windows OS Troubleshooting, Help Desk Support, Ticketing/ITSM, DNS`

To add a tool's canonical form, edit `canonical_casing` in the YAML.

---

## 2. Skills list hygiene

A padded, duplicated, mixed-case skills line reads as keyword-stuffing to a human
even when it helps ATS.

- **De-duplicate by concept, not just by string.** `ticketing`, `ITSM`,
  `Ticketing / ITSM`, `Help desk ticketing` all collapse to one `Ticketing/ITSM`
  via the `synonym_map`. *(autofix)*
- **Cap the lists.** Technical 12–22 items, soft 6–12. Beyond the cap, the
  lowest-priority items are dropped. *(autofix)*
- **Drop padding tags.** `Wireshark (study)`, `Sandboxie-Plus (study)` are dropped
  unless the JD specifically names the tool, in which case the tag is stripped and
  the skill kept. *(autofix)*
- **Strip orphan fragments** like `(user-level)`, `(basic)`, empty `()`. *(autofix)*
- **A surfaced JD keyword belongs in BOTH the summary and the skills list** so an
  ATS keyword scan never misses it. *(handled upstream in `resume_tailor.py`; the
  linter enforces the casing/dedupe once it's there.)*

---

## 3. Banned & weak phrasing

Maintained as lexicons in `style_rules.yaml → banned_phrases`, grouped by class so
each class can have its own severity. Add new offenders to the YAML, not the code.

| Class | Examples | Severity | Action |
|---|---|---|---|
| **Hype** | revolutionized, transformed, world-class, synergy, single-handedly, game-changer, ninja, rockstar | warn | flag (autofix when a safe replacement exists) |
| **Vague verbs** | leveraged, utilized, spearheaded, orchestrated, facilitated | autofix | → used / led / coordinated / ran |
| **Hedges** | help-desk-adjacent, "-adjacent", somewhat, I believe, while my background is not, at the user level only | warn | flag (these *weaken* confidence) |
| **Informal** | break/fix work, stuff, a lot of, kinda, gonna | warn | flag (too casual) |
| **Clichés** | team player, detail-oriented, proven track record, passionate about, hit the ground running | warn | flag |
| **AI tells** | perfectly aligns, confident in my ability, delve, tapestry, testament to, in today's fast-paced world | warn | flag (autofix some) |
| **Generic openers** (CL) | "I am writing to apply", "To whom it may concern", "Please find attached" | warn | flag |
| **Groveling** (CL) | "I would be honored", "at your earliest convenience", "thank you for considering", humbly | warn | flag (undersells) |

> Wrong: `I leveraged Microsoft 365 and have help-desk-adjacent experience.`
> Right: `I used Microsoft 365 and have practical help desk and technical operations experience.`

Prefer concrete action verbs: built, supported, resolved, troubleshot, documented,
reduced, coordinated, improved, maintained, configured, deployed, administered.

---

## 4. Resume bullets

- **One idea per bullet.** A bullet joined by a semicolon is automatically **split
  into two**. *(autofix)*

  > Wrong: `Managed help desk requests in a ticket system and improved onboarding through documentation; handled support requests with clear communication.`
  > Right (two bullets): `Managed help desk requests in a ticket system, documented actions taken, and communicated clearly with users.` / `Authored onboarding documentation and SOPs that reduced ramp time.`

- **Start with an action verb.** Bullets that open with *Responsible for*, *Duties
  included*, *Worked on*, *Helped with*, or with an article/pronoun are flagged. *(warn)*
- **No first person.** Resume bullets never use I/my/me. *(warn)*
- **Length cap** ~240 chars / ~34 words; longer bullets are flagged for tightening. *(warn)*
- **3–6 bullets per role.** Outside that range is flagged. *(warn)*
- **No slash-salad** (more than 2 slashes in one line — `Ticketing / ITSM, PST /
  time-zone` reads as fragments). *(warn)*
- **No dangling conjunction** ("…and", "…to" at the end of a bullet). *(warn)*

---

## 5. Resume summary

- **Open with the target title**, never a generic label. `Remote candidate with…`
  is rewritten to `Service Desk Technician candidate with…`. *(autofix)*
  Forbidden generic openers: *Remote candidate, Experienced candidate, Motivated
  professional, Results-driven, Dedicated/Seasoned professional, A candidate*.
- **No comma/slash salad.** A verbless string of keyword fragments
  (`…aligned with mission, communication, customer while delivering supported
  experience in Ticketing / ITSM, PST / time-zone coverage.`) is **blocked** and
  must be regenerated. *(block)*
- **2–4 sentences, ≤ 520 chars, no first person.** *(warn on length)*

> Wrong: `Remote candidate with hands-on end-user support…`
> Right: `Service Desk Technician candidate with hands-on experience in end-user support, Windows troubleshooting, ticketing systems, and SOP/runbook documentation.`

---

## 6. Punctuation & typography

- **Smart quotes → straight quotes** (ATS-safe). *(autofix)*
- **No double spaces; no space before punctuation; no trailing whitespace.** *(autofix)*
- **Date ranges use an en dash** (`Sept 2024 – Mar 2026`), not a hyphen or `--`. *(autofix)*
- **No DB-format dates** (`2024-03`) leaking into candidate-facing prose. *(warn)*
- **Collapse repeated punctuation** (`!!`, `..`, `?!`). *(autofix)*
- **Oxford comma** in serial lists (heuristic). *(warn)*

---

## 7. Cover letter

Schema: `{opening, body_paragraphs[], closing, proof_targets[]}`. The salutation and
signoff are added at export, so greeting checks here are advisory.

- **180–400 words, 2–3 body paragraphs**, one theme each. *(warn outside range)*
- **Names the company and the role** at least once. Missing either is **blocked**
  (a wrong-company / unnamed-role letter is a credibility killer). *(block)*
- **No generic opener** ("I am writing to apply…"). Lead with the single strongest,
  specific fit hook. *(warn)*
- **No groveling closer.** End with a confident, concrete next step, not "I would be
  honored… at your earliest convenience." *(warn)*
- **Professional tone**, not casual ("makes the service desk feel more than just
  break/fix work" → "makes this service desk role more than simple break/fix
  support"). *(warn/autofix)*
- **Not first-person-heavy** — fewer than ~40% of sentences should start with "I". *(warn)*
- **Does not repeat resume bullets verbatim** (see §8). *(warn)*
- **States the candidate's ACTUAL years of experience**, never the JD's required band. *(handled in tailor; cross-checked in §8)*

> Wrong: `Digitech's work makes the service desk feel more than just break/fix work.`
> Right: `Digitech's work supporting EMS billing and compliance makes this service desk role more than simple break/fix support.`

---

## 8. Cross-document consistency (resume ↔ cover letter)

A recruiter reads both together; mismatches are glaring.

- **Same target title** referenced in both. *(warn)*
- **Same years-of-experience figure** in both (`3+` on the resume, not `5+` in the
  letter). *(warn)*
- **Same company name spelling**; the letter references the package's target company. *(warn)*
- **No verbatim reuse** — the cover letter must not paste a resume bullet word-for-word. *(warn)*

---

## 9. Parser / ATS sanity

- **No garbage glyphs** (`�`, replacement chars, zero-width spaces). *(block)*
- **Every experience entry has a readable company and title.** *(block)*
- **One-line, parser-safe job headers** (avoid columns the parser scrambles).
- **Under two pages.**

---

## 10. What stays with the LLM (not in this file)

The deterministic layer above cannot judge these — they remain the job of the
critique loop and the calibrated judge, guided by prompts:

- Is the framing **credible** for this candidate and seniority?
- Is each bullet **relevant** to *this* JD, or noise?
- Does the cover letter **mirror the company's voice** and address the real proof targets?
- Is the writing **persuasive** and well-sequenced?

And the **truth layer** always has the final say on *whether a claim may be made at
all*. The presentation linter only governs *how a permitted claim is worded*.

---

## How to extend

1. Found a new recurring defect? Decide its layer (deterministic / judgment / truth).
2. If deterministic: add it to `style_rules.yaml` (a phrase to `banned_phrases`, a
   mapping to `canonical_casing`/`synonym_map`, a threshold to `bullets`/`summary`),
   set its severity in `severity_defaults`, and add a fixture under `tests/`.
3. If judgment: add it to the critique/judge prompt and the rubric.
4. Re-run the golden corpus to confirm no regressions.

The defect list should only ever **shrink**: once a problem is a rule + a test, it
can't silently come back.
