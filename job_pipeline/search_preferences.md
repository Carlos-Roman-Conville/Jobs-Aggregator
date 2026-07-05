# Search Preferences — Carlos D. Roman-Conville

> This file is the hand-edited authority for what jobs the pipeline should
> prioritize during SEARCH and SCORING. It sits alongside `career_master.md`
> (resume tailoring grounding) and `consolidated_profile.md` (resume-derived
> identity). It does NOT govern resume tailoring — only what shows up at the
> top of the queue and what gets auto-closed before review.
>
> Mirror of the precedence pattern used for `career_master.md`: this file
> survives re-bootstrap, is read first by `job_pipeline.search_preferences`,
> and is the authoritative source of truth for the search/scoring layer.
> Edit by hand whenever search behavior should change.
>
> Honesty rules apply: list what you would actually accept and apply to in
> good faith. The pipeline will *act* on these floors and rejects.

## Location priority (ranked, highest first)

1. Remote (fully remote, anywhere US) — STRONGLY PREFERRED, push to top of queue
2. Hybrid within 30 miles of Philadelphia, PA 19107 — acceptable, must be
   tech-related
3. Onsite within 30 miles of Philadelphia, PA 19107 — **IN SCOPE for hands-on tech roles** (no longer last-resort):
   - The role must be technical (IT support, helpdesk, NOC, desktop
     support, network tech, AV tech, CCTV/security camera tech,
     low-voltage tech, electronics repair / bench tech, depot repair,
     computer repair shop, field service tech) — NOT general operations
   - Salary floor for onsite: $40,000/year (lowered from $70K — income urgency)

Reject anything onsite or hybrid outside the 30-mile Philadelphia radius
unless explicitly added to an exceptions list.

**Proximity rule (added):** Within the 30-mile radius, the closer a posting
is to ZIP 19107 (Center City Philadelphia, home base), the higher its
score. Use the distance table in the "Geography constraints" section
below. Closer == better. This is a graduated score boost on top of the
work-mode multiplier.

## Salary floors

- Remote: no hard floor — anything genuinely paid is acceptable, including
  postings at $50,000 and below. (The noise filter still rejects "unpaid",
  "commission only", "1099 contractor", "MLM", and gig-platform piecework
  like "AI Trainer" / "data annotation".)
- Hybrid: $40,000/year minimum (lowered from $50K — income urgency).
- Onsite: $40,000/year minimum (lowered from $50K — income urgency).

Hourly postings: convert to annual at 2,080 hours/year. Hybrid/onsite
postings below the above floors should be auto-closed with reason
"salary_below_floor". Remote postings are not closed on salary — they
just rank lower if pay is weak.

## Salary preference (soft boost — applies on top of floors)

Strongly prefer postings at or above **$70,000/year**. A posting at or
above this threshold gets a meaningful score boost regardless of work
mode. This is a SOFT preference — postings between the floor and $70k
are still acceptable if they survive the floor and other rules; they
just rank lower than $70k+ postings.

## Career growth signal (boost roles that have it)

Boost a posting in the queue when the JD contains any of these signals:

- Explicit ladder language ("Junior → Mid → Senior", "promotion path",
  "career development plan", "growth track")
- Certification reimbursement (CompTIA, Microsoft, Cisco, RedHat, AWS)
- Mentorship language ("paired with senior", "shadow rotation", "rotation
  program")
- Cross-training offered
- "Path to systems administration", "path to network engineering", etc.

This is a SOFT preference — score bump, not a hard filter.

## Target role families (TIER 1 ONLY — hard cap, see "Avoid" below)

**A — strongest fit, prioritize:**

- Desktop Support Technician / Desktop Support Specialist
- IT Support Specialist / IT Support Technician / IT Support I
- Help Desk Analyst / Help Desk Technician / Help Desk I
- Junior Systems Administrator / Jr Sysadmin
- NOC Technician (Junior / Level 1 only)
- IT Operations Technician
- Field Service Technician (IT/AV/general flavor)
- Technical Support Specialist I
- Customer Support Engineer I (technical-flavored only)

**B — widened scope (Tier 1 across the whole tech spectrum):**

- AV Technician / Audio Visual Technician / AV Install Tech / AV/IT
- CCTV Technician / Security Camera Tech (maintenance + replacement install)
- Low-Voltage Technician / Cable Tech / Cabling Installer
- Electronics Repair Technician / Bench Tech / Depot Repair Tech
- Computer Repair Technician (uBreakiFix-tier shops)
- Systems Support Technician
- Network Technician (entry/junior level)
- IT Specialist (especially federal — see USAJOBS)
- Computer Technician / Endpoint Technician / Workstation Support
- Onsite IT Technician
- Implementation Specialist (Tier 1 / I only, technical setup flavor)

**C — Management / Operations family (Carlos has 4.5 yrs documented Ops Manager experience: BTB + 1-800-GOT-JUNK):**

- Operations Manager / Ops Manager / Technical Operations Manager
- IT Manager / Help Desk Manager / Service Desk Manager / Support Manager
- IT Operations Manager
- Office Manager / Store Manager / Shift Manager / Floor Manager / Venue Manager / Restaurant Manager / General Manager
- Facilities Manager / Service Manager / Customer Service Manager
- Account Manager / Implementation Manager
- Shift Lead / Team Lead (non-engineering)

This family has NO tier-1 cap (managers aren't tiered the same way IT-IC roles are).
Reject still applies to: Senior/Sr./Principal/Staff/Director/VP, Engineering Manager,
Software Manager.

**D — Political / Policy / Advocacy / Think-tank (Poli Sci BA Cum Laude + 2021 paid canvass cycle + Reserve multi-agency coordination grounding):**

- Policy Assistant / Policy Associate / Junior Policy Analyst / Policy Coordinator
- Research Assistant / Research Associate (think-tank or policy)
- Legislative Aide / Legislative Assistant / Legislative Correspondent / Legislative Analyst / Legislative Coordinator
- Government Affairs Coordinator / Government Relations Coordinator / Government Affairs Associate
- Public Affairs Assistant / Public Affairs Associate / Public Affairs Coordinator
- Communications Assistant / Communications Associate / Communications Coordinator (political/advocacy)
- Program Assistant / Program Coordinator / Program Associate (advocacy/nonprofit)
- Outreach Coordinator (non-canvassing)
- Civic Tech / Civic Engagement Coordinator
- Think tank entry roles

**Manager-of-canvassing IS OK** (Carlos accepts these — his rule):
- Field Director / Canvass Director / Canvass Manager
- Volunteer Coordinator / Organizing Director
- Voter Contact Manager

**REJECT (IC canvassing — Carlos's explicit rule):**
- Canvasser
- Field Organizer (IC role — typically heavy door-to-door)
- Field Representative
- Door-to-door / D2D
- Petitioner / Signature Gatherer / Petition Gatherer
- Phone Banker / Phone Bank Operator

**E — Civic Tech / GovTech / Political-Tech (best-fit niche — sits at the intersection of Poli Sci BA + tech ops record + vet status):**

- Civic Tech / Civic Technology / Civic Engagement Technology
- GovTech / Government Technology
- Public Sector Tech / Public Sector IT
- Election Technology / Election Systems / Election Security
- Voter File Admin / Voter Data Analyst / Voter Registration Tech
- Political Data Analyst / Political Tech / Campaign Tech Ops
- USDS (US Digital Service), 18F, Code for America — direct civic-tech orgs
- Truss, Nava PBC, Ad Hoc — federal civic-tech contractors
- NGP VAN admin, NationBuilder admin, ActionNetwork admin — political CRM
- Federal IT Specialist (USAJOBS 2210 series) with vet preference — civic mission
- City of Philadelphia / State of PA IT roles (local + civic + Carlos's preferred metro)
- Policy/research org IT support: Brookings, Pew Research, RAND, Urban Institute, New America, EFF, CDT

This niche uniquely values: "Poli Sci BA + tech ops + veteran" = Carlos's exact profile.
Same Tier 1 cap applies (no Senior/Lead/Director/etc.). Same canvass rule (mgmt OK, IC out).

## Avoid (hard reject these titles)

- **TIER CAP (IC support roles only):** anything titled `Tier 2`, `Tier II`,
  `Level 2`, `Level II`, `L2` (when meaning Level 2), `Specialist II`,
  `Specialist 2`, `Tier 3`, `Level 3` — Carlos is capped at Tier 1 for IT-IC.
  Management/Ops titles are NOT subject to this cap.
- Anything with Senior, Sr., Principal, Staff, Director, VP, Head of
- Engineering Manager / Software Manager (too senior for the engineering side)
- Project Manager (without Technical prefix)
- DevOps Engineer / SRE / Cloud Engineer / Cloud Architect
- Software Engineer / Backend / Frontend / Full Stack Engineer
- Sales Engineer (sales-led roles)
- Solutions Architect
- Pure ServiceNow Administrator (specialist platform admin)
- Pure Identity & Access Management (IAM) roles
- Pure Security Engineer roles
- AI Trainer / Data Annotation / Data Labeler / AI Tutor / Prompt
  Engineer (gig-platform piecework)
- Anything tagged 1099 or "contributor" without W2 / benefits
- Pure call center / customer service rep without IT scope
- Warehouse / fulfillment / logistics ops
- Retail anything
- **NOT GROUNDED — explicit reject:** Phone hardware repair (user-level
  mobile only), greenfield security alarm install (CCTV-adjacent only),
  greenfield in-wall cable runs, microsoldering / SMT / BGA rework
  (through-hole only — see career_master.md L199).
- **IC CANVASSING — explicit reject (Carlos's rule):** Canvasser, Field
  Organizer (IC), Field Representative (IC), Door-to-door / D2D,
  Petitioner, Signature/Petition Gatherer, Phone Banker. Canvass MANAGEMENT
  (Field Director, Canvass Manager, Organizing Director, Volunteer
  Coordinator) IS accepted — see Section D above.

**Removed from this list (2026-05-31):** Operations Manager (now accepted —
4.5 yrs grounding); Facilities Manager (now accepted); generic "Lead" alone
(was false-positiving on Shift Lead / Team Lead).

## Veteran-preference lane (boost when present)

Roles flagged as accepting veteran preference or posted via USAJOBS get
a score boost. Carlos has 8 years U.S. Army Reserve (68W Combat Medic,
338th Medical Brigade) — qualifies for vet preference on federal hires.

## Geography constraints (for hybrid/onsite filter)

Home base: Philadelphia, PA 19107
Acceptable radius: 30 miles (covers Center City Philly, South Philly,
Northeast Philly, parts of Delaware County, parts of Camden County NJ,
parts of Montgomery County PA, parts of Bucks County PA)

Specific corridors that count as "Philly area": Wilmington DE, King of
Prussia PA, Fort Washington PA, Bensalem PA, Cherry Hill NJ.

Outside this radius = onsite/hybrid HARD REJECT.

### Proximity distance table (approximate driving miles from 19107)

Used by the scorer to compute a closer-is-better proximity boost.
Within the 30-mile radius only; outside is a hard reject regardless.

| Distance band | Miles from 19107 | Example locations                                  | Proximity multiplier |
|---------------|------------------|----------------------------------------------------|----------------------|
| Inner core    | 0 – 5            | Center City, South Philly, University City, Fishtown, Northern Liberties | 1.10                 |
| Near ring     | 5 – 10           | West Philly, Northeast Philly inner, Camden NJ, Bala Cynwyd, Upper Darby  | 1.07                 |
| Mid ring      | 10 – 20          | Cherry Hill NJ, Conshohocken, Bensalem, Plymouth Meeting, Media          | 1.04                 |
| Outer ring    | 20 – 30          | King of Prussia, Fort Washington, Wilmington DE, Princeton NJ outskirts  | 1.01                 |
| Outside       | > 30             | (hard reject for hybrid/onsite — does not apply)   | n/a                  |

Remote postings do not get a proximity boost (location is "anywhere"),
but they already get the remote multiplier and that stacks normally.

## Search-term seed list (for ingest sources that take search terms)

Use these as the search_term values when configuring JobSpy, USAJobs,
Greenhouse, Lever, etc.:

NARROWED 2026-05-31: pipeline restricted to Carlos's 5 primary targets + Ops Manager backup.
All other categories (hands-on tech, civic-tech, political, AI, healthcare-specific) removed.

PRIMARY targets (boosted in ranking):

- "help desk"
- "help desk analyst"
- "help desk technician"
- "service desk"
- "service desk analyst"
- "customer support engineer"
- "customer support specialist"
- "technical support specialist"
- "technical support engineer"
- "tech support"
- "junior IT support"
- "jr IT support"
- "IT support"
- "IT support specialist"
- "IT specialist"
- "information technology specialist"
- "support services representative"
- "desktop support"
- "desktop support technician"
- "deskside support"
- "NOC tier 1"
- "NOC analyst"
- "NOC technician"
- "junior NOC"

BACKUP target (still in pipeline, lower boost):

- "operations manager"
- "ops manager"
- "technical operations manager"
- "IT operations manager"
- "IT manager"
- "service desk manager"
- "help desk manager"
- "support manager"

WIDENED 2026-06-20 (scope expansion — maximize remote job inflow across all lanes
Carlos would take; categorized in the dashboard by lane_category.py):

Customer service / support (high-volume, fast-hire remote):
- "customer service representative"
- "customer service specialist"
- "remote customer service"
- "customer success specialist"
- "customer success associate"
- "client support specialist"
- "product support specialist"
- "member support specialist"

Application / broader IT support:
- "application support analyst"
- "application support specialist"
- "technical support representative"
- "technical support analyst"
- "IT technician"
- "IT support technician"
- "IT analyst"

Sysadmin / network (IT General lane):
- "systems administrator"
- "junior systems administrator"
- "systems analyst"
- "network technician"
- "network administrator"

Operations (broader):
- "operations coordinator"
- "operations analyst"
- "operations associate"
- "project coordinator"
- "implementation specialist"
- "onboarding specialist"
- "operations specialist"
- "operations support analyst"
- "operations administrator"
- "business operations associate"
- "implementation coordinator"
- "client success associate"
- "logistics coordinator"
- "office manager"
- "program coordinator"
- "service coordinator"

General / entry remote:
- "data entry specialist"
- "virtual assistant"
- "remote support specialist"

ADJACENT-CAREER 2026-06-20 (Carlos's hybrid background — teaching/docs/policy — qualifies for
these beyond IT helpdesk; surface them so we can decide on dedicated tabs once volume is real):

Technical Writer / Documentation (BTB runbook/SOP/KB authoring + 3.80 Poli Sci writing degree):
- "technical writer"
- "documentation specialist"
- "technical documentation"
- "knowledge base specialist"
- "content designer"

Technical Trainer / Enablement / Customer Education (BTB trained every hire + 3yr Army instructor):
- "technical trainer"
- "product trainer"
- "customer enablement"
- "customer education"
- "training specialist"
- "learning and development specialist"

GovTech / Civic-software vendor support+implementation (Poli Sci + IT + bilingual; NOT direct-federal
which needs clearance — these are private vendors selling to government):
- "govtech"
- "civic technology"
- "public sector support"
- "government software"

## Boolean-style noise filters (apply to JD body before scoring)

Strongly down-rank or auto-close postings whose body contains:

- "AI Trainer"
- "data annotation"
- "data labeler"
- "1099 contractor" (no benefits)
- "commission only"
- "MLM" / "multi-level marketing"
- "unpaid"
- "internship" (unless paid > salary floor)
