"""
Hand-curated seed of high-fit scholarships for Carlos's profile.

Profile fit signals used:
  - Reservist veteran (Army Reserve 2011-2020, honorable, NOT active-duty)
  - Hispanic (PR-born, fluent comprehension)
  - PA resident
  - WGU BS Cybersecurity in progress (admitted, transcripts sent)
  - Cum Laude BA Political Science (Rowan, 3.80 GPA)
  - Non-traditional / adult / career-changer
  - Pursuing CompTIA A+/Network+/Security+ via PA WIOA

Each entry below was identified from research in this session. Award amounts
and deadlines reflect what was published as of 2026-06-03; Carlos MUST verify
on the sponsor's official page before applying. Entries are marked with
`needs_verification` flag in eligibility_criteria so the scoring/UI surfaces this.

Run:
    from scholarship_pipeline.sources.curated_seed import seed_curated
    n_loaded = seed_curated()
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from scholarship_pipeline.db import upsert_posting


def _dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 23, 59, 0, tzinfo=timezone.utc)


# Each dict is a kwargs payload for upsert_posting. The `external_id` is a
# stable handle so re-running this seeder is idempotent.
_CURATED: List[Dict[str, Any]] = [
    # --- DEEP-RESEARCH-VERIFIED ADDITIONS (2026-06-07) --------------------
    {
        "source": "curated",
        "external_id": "dod-cyber-scholarship-program-cysp",
        "title": "DoD Cyber Scholarship Program (CySP)",
        "provider": "U.S. Department of Defense",
        "description_text": (
            "Full tuition + ~$27,000 annual stipend for junior/senior "
            "undergraduate and graduate cybersecurity students at "
            "participating institutions. WGU is independently confirmed as "
            "a participating institution (per WGU's 2023 press release and "
            "CySP fact sheet on wgu.edu). Service obligation: federal "
            "cybersecurity employment after graduation, typically year-for-year. "
            "Highest-dollar verified pathway for Carlos."
        ),
        "apply_url": "https://www.wgu.edu/content/dam/wgu-65-assets/western-governors/documents/it/CySP.pdf",
        "award_amount_min": 27000,
        "award_amount_max": 50000,
        "deadline_at": None,
        "rolling_deadline": False,
        "renewable": True,
        "degree_level": "undergraduate",
        "field_of_study": "cybersecurity",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Must be enrolled at a participating institution (WGU confirmed). "
            "Junior/senior undergraduate or graduate cybersecurity student. "
            "Federal service obligation post-graduation. Carlos must enroll "
            "+ progress to junior standing in his BSCIA program first. "
            "needs_verification: confirm current cycle dates and whether "
            "WGU is accepting new CySP applications for AY 2026-27 directly "
            "with the WGU enrollment counselor."
        ),
        "essay_required": True,
        "transcript_required": True,
    },
    {
        "source": "curated",
        "external_id": "bold-org-be-bold-no-essay-25k",
        "title": "Bold.org Be Bold No-Essay Scholarship ($25,000)",
        "provider": "Bold.org",
        "description_text": (
            "Single $25,000 no-essay scholarship. Apply with just a "
            "Bold.org profile. Awarded to students 'who are bold' — "
            "essentially a high-value lottery with zero essay cost. Bold.org "
            "platform-level rules have NO second-bachelor's exclusion."
        ),
        "apply_url": "https://bold.org/scholarships/be-bold-no-essay-scholarship/",
        "award_amount_min": 25000,
        "award_amount_max": 25000,
        "deadline_at": _dt(2026, 6, 30),
        "rolling_deadline": False,
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Open to any US student. Second-bachelor's NOT excluded per "
            "Bold.org's platform rules. Zero essay = zero per-application cost. "
            "Pure lottery odds but $25K upside. needs_verification: confirm "
            "current cycle on Bold.org."
        ),
        "essay_required": False,
    },
    {
        "source": "curated",
        "external_id": "bold-org-sallie-no-essay-2k",
        "title": "Sallie No-Essay Scholarship ($2,000, monthly)",
        "provider": "Sallie Mae via Bold.org",
        "description_text": (
            "Monthly drawing $2,000 scholarship hosted on Bold.org. No essay. "
            "New cycle each month."
        ),
        "apply_url": "https://bold.org/scholarships/sallie-no-essay-scholarship/",
        "award_amount_min": 2000,
        "award_amount_max": 2000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "any",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Open to any US student of any age/level. Monthly drawing. "
            "Second-bachelor's eligible. Zero-essay re-apply each month."
        ),
        "essay_required": False,
    },
    # --- WGU-INTERNAL (highest fit, smallest competition pool) -------------
    {
        "source": "curated",
        "external_id": "wgu-cybersecurity-scholarship",
        "title": "WGU Cybersecurity Scholarship",
        "provider": "Western Governors University",
        "description_text": (
            "WGU-internal scholarship for students pursuing the BS or MS in "
            "Cybersecurity and Information Assurance. Awarded at $1,500 per "
            "6-month term, renewable for up to four terms (total up to $6,000)."
        ),
        "apply_url": "https://www.wgu.edu/financial-aid-tuition/scholarships/general/cybersecurity.html",
        "award_amount_min": 1500,
        "award_amount_max": 6000,
        "deadline_at": None,
        "rolling_deadline": True,
        "renewable": True,
        "degree_level": "undergraduate",
        "field_of_study": "cybersecurity",
        "geographic_restriction": "any",
        "eligibility_criteria": (
            "Must be admitted to a WGU Cybersecurity degree program. Rolling "
            "evaluation. Carlos's status: admitted, transcripts sent 2026-06. "
            "needs_verification: confirm current per-term cap on WGU portal."
        ),
        "essay_required": True,
        "essay_prompt": "Personal statement about your interest in cybersecurity and career goals (typically 500-750 words).",
    },
    {
        "source": "curated",
        "external_id": "wgu-dow-csa",
        "title": "DOW CSA Scholarship at WGU",
        "provider": "WGU / DOW Cybersecurity Awards",
        "description_text": (
            "WGU's diversity-in-cybersecurity scholarship program. "
            "Deadline historically falls February 17 of the academic-year "
            "recruitment cycle."
        ),
        "apply_url": "https://www.wgu.edu/lp/it/wgu/cyber-scholarship-program.html",
        "award_amount_min": 1000,
        "award_amount_max": 5000,
        "deadline_at": _dt(2027, 2, 17),
        "degree_level": "undergraduate",
        "field_of_study": "cybersecurity",
        "geographic_restriction": "any",
        "eligibility_criteria": (
            "Diversity-focused cybersecurity scholarship at WGU. Hispanic + "
            "veteran status both qualify. needs_verification: confirm 2026-27 "
            "cycle dates and exact eligibility on the WGU sponsor page."
        ),
        "essay_required": True,
    },
    {
        "source": "curated",
        "external_id": "wgu-cybersecurity-diversity-foundation",
        "title": "Cybersecurity Diversity Foundation + WGU Scholarship",
        "provider": "Cybersecurity Diversity Foundation",
        "description_text": (
            "Partnership scholarship covering two full years of tuition. "
            "Historically advertised for master's degrees; verify BS eligibility."
        ),
        "apply_url": "https://www.hispanicoutlook.com/articles/12000-scholarships-available-through-wgu-and-cyber",
        "award_amount_min": 12000,
        "award_amount_max": 12000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "graduate",
        "field_of_study": "cybersecurity",
        "geographic_restriction": "any",
        "eligibility_criteria": (
            "Diversity-in-cybersec. needs_verification: confirm BS-level "
            "eligibility (originally a master's program scholarship)."
        ),
        "essay_required": True,
    },
    # --- VETERAN ----------------------------------------------------------
    {
        "source": "curated",
        "external_id": "amvets-national-scholarship",
        "title": "AMVETS National Scholarship - Veteran/Reserve/Active-Duty",
        "provider": "AMVETS National Service Foundation",
        "description_text": (
            "Annual $4,000 scholarship for veterans, active-duty, National "
            "Guard, and Reservists pursuing a bachelor's degree. Carlos's "
            "Reservist 2011-2020 honorable discharge qualifies."
        ),
        "apply_url": "https://www.amvets.org/scholarships",
        "award_amount_min": 4000,
        "award_amount_max": 4000,
        "deadline_at": _dt(2027, 4, 30),
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Open Feb 2 - April 30 annually. Carlos qualifies: Reservist, "
            "Honorable discharge, pursuing BS. needs_verification: confirm "
            "exact 2027 cycle dates on AMVETS portal."
        ),
        "essay_required": True,
        "transcript_required": True,
    },
    {
        "source": "curated",
        "external_id": "vfw-sport-clips-help-a-hero",
        "title": "VFW \"Sport Clips Help A Hero\" Scholarship",
        "provider": "VFW Foundation + Sport Clips Haircuts",
        "description_text": (
            "Up to $5,000 scholarship for post-9/11 veterans pursuing "
            "post-secondary education. Multiple cycles per year."
        ),
        "apply_url": "https://www.vfw.org/sportclipsscholarship",
        "award_amount_min": 500,
        "award_amount_max": 5000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Post-9/11 vet/service-member. Carlos qualifies (2011-2020 service "
            "= post-9/11 era). Multiple application windows per year. "
            "needs_verification: confirm current cycle dates."
        ),
        "essay_required": True,
    },
    {
        "source": "curated",
        "external_id": "tillman-military-scholar",
        "title": "Pat Tillman Foundation Tillman Scholar Program",
        "provider": "Pat Tillman Foundation",
        "description_text": (
            "Premier veteran scholarship - $10,000+ award plus lifetime alumni "
            "network access. Highly competitive (~60 selected from ~5,000 apps). "
            "Targets service members, veterans, and military spouses with "
            "leadership trajectory."
        ),
        "apply_url": "https://pattillmanfoundation.org/apply-to-be-a-tillman-scholar/",
        "award_amount_min": 10000,
        "award_amount_max": 30000,
        "deadline_at": _dt(2027, 2, 28),
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Service member / veteran / military spouse. Carlos qualifies. "
            "Application historically opens January, closes late February. "
            "Highly competitive but worth shooting for. needs_verification: "
            "confirm 2027 cycle dates."
        ),
        "essay_required": True,
        "essay_word_min": 500,
        "essay_word_max": 1000,
        "recommendations_required": 2,
        "transcript_required": True,
    },
    # --- HISPANIC ---------------------------------------------------------
    {
        "source": "curated",
        "external_id": "hispanic-scholarship-fund",
        "title": "Hispanic Scholarship Fund (HSF) General Scholarship",
        "provider": "Hispanic Scholarship Fund",
        "description_text": (
            "HSF's flagship program. $500-$5,000 awards. Carlos qualifies "
            "(PR-born, Hispanic). Cum Laude GPA strengthens the application."
        ),
        "apply_url": "https://www.hsf.net/scholarship",
        "award_amount_min": 500,
        "award_amount_max": 5000,
        "deadline_at": _dt(2027, 2, 15),
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Hispanic heritage required. Min GPA 3.0. Carlos: 3.80 Cum Laude. "
            "Application opens January annually. needs_verification: confirm "
            "2027 dates."
        ),
        "min_gpa": 3.0,
        "essay_required": True,
        "essay_word_min": 300,
        "essay_word_max": 500,
    },
    {
        "source": "curated",
        "external_id": "henaac-great-minds-stem",
        "title": "HENAAC Scholars Program (Great Minds in STEM)",
        "provider": "Great Minds in STEM",
        "description_text": (
            "Hispanic-focused STEM scholarship including cybersecurity. "
            "$500-$10,000 awards. Need-based and merit-based tracks."
        ),
        "apply_url": "https://www.greatmindsinstem.org/scholarships",
        "award_amount_min": 500,
        "award_amount_max": 10000,
        "deadline_at": _dt(2027, 4, 30),
        "degree_level": "undergraduate",
        "field_of_study": "STEM",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Hispanic + STEM major. Carlos qualifies (Hispanic + cybersecurity). "
            "needs_verification: confirm 2027 cycle dates and minimum GPA."
        ),
        "min_gpa": 3.0,
        "essay_required": True,
    },
    # --- CYBERSECURITY-SPECIFIC -------------------------------------------
    {
        "source": "curated",
        "external_id": "isc2-undergraduate-cybersecurity",
        "title": "(ISC)² Undergraduate Cybersecurity Scholarship",
        "provider": "(ISC)² Center for Cyber Safety and Education",
        "description_text": (
            "Up to $5,000 for undergraduate cybersecurity students. Multiple "
            "named tracks (women in cyber, diversity, etc.). Awards announced "
            "in May/June."
        ),
        "apply_url": "https://www.iamcybersafe.org/s/scholarships",
        "award_amount_min": 1000,
        "award_amount_max": 5000,
        "deadline_at": _dt(2027, 3, 1),
        "degree_level": "undergraduate",
        "field_of_study": "cybersecurity",
        "geographic_restriction": "any",
        "eligibility_criteria": (
            "Undergrad pursuing cybersecurity-related degree. needs_verification: "
            "confirm 2027 cycle dates and specific track eligibility."
        ),
        "min_gpa": 3.3,
        "essay_required": True,
        "transcript_required": True,
    },
    {
        "source": "curated",
        "external_id": "national-cyber-scholarship-foundation",
        "title": "National Cyber Scholarship Foundation",
        "provider": "NCSF / SANS Institute",
        "description_text": (
            "SANS-affiliated cybersecurity scholarship. Provides training, "
            "certifications, and education funding for top performers in the "
            "National Cyber Scholarship Competition (CyberStart America)."
        ),
        "apply_url": "https://www.nationalcyberscholarship.org/",
        "award_amount_min": 2500,
        "award_amount_max": 25000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "undergraduate",
        "field_of_study": "cybersecurity",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "US citizen/PR. Carlos qualifies. Often requires completion of "
            "CyberStart America competition. needs_verification: confirm "
            "current eligibility and competition requirements."
        ),
        "essay_required": True,
    },
    # --- ADULT LEARNER / NON-TRADITIONAL ----------------------------------
    {
        "source": "curated",
        "external_id": "imagine-america-adult-skills",
        "title": "Imagine America Foundation - Adult Skills Education Award",
        "provider": "Imagine America Foundation",
        "description_text": (
            "$1,000 for adult learners (19+) pursuing career-focused education. "
            "Targets non-traditional students returning to school."
        ),
        "apply_url": "https://imagine-america.org/students/scholarships-education/",
        "award_amount_min": 1000,
        "award_amount_max": 1000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Age 19+, US citizen, enrolled in qualifying career-focused program. "
            "Carlos qualifies. needs_verification: confirm WGU is on their "
            "list of participating schools."
        ),
        "essay_required": True,
    },
    # --- PA STATE ---------------------------------------------------------
    {
        "source": "curated",
        "external_id": "pheaa-pa-state-grant",
        "title": "PHEAA PA State Grant",
        "provider": "Pennsylvania Higher Education Assistance Agency",
        "description_text": (
            "Pennsylvania's largest need-based state grant. Up to $5,750 per "
            "year. FAFSA + PHEAA application required."
        ),
        "apply_url": "https://www.pheaa.org/funding-opportunities/state-grant-program/",
        "award_amount_min": 500,
        "award_amount_max": 5750,
        "deadline_at": _dt(2027, 5, 1),
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "PA",
        "eligibility_criteria": (
            "PA resident. Need-based (FAFSA-tied). WGU is approved institution. "
            "Carlos qualifies. needs_verification: confirm 2027-28 deadline "
            "on PHEAA portal (historically May 1)."
        ),
        "renewable": True,
        "essay_required": False,
        "transcript_required": False,
    },
    # --- BROAD APPLICATION (no demographic gate) --------------------------
    {
        "source": "curated",
        "external_id": "sallie-mae-bridging-the-dream",
        "title": "Sallie Mae Bridging the Dream Scholarship",
        "provider": "Sallie Mae",
        "description_text": (
            "$1,000 monthly drawing for undergraduate students. No essay "
            "required. Quick application."
        ),
        "apply_url": "https://www.salliemae.com/scholarships/",
        "award_amount_min": 1000,
        "award_amount_max": 1000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Any US undergrad, monthly recurring drawings. Low effort, low "
            "probability, worth applying. needs_verification: confirm current "
            "drawing cycle on Sallie Mae portal."
        ),
        "essay_required": False,
    },
    {
        "source": "curated",
        "external_id": "bold-org-veteran-next-gen",
        "title": "Veterans Next Generation Scholarship",
        "provider": "Bold.org (Veterans Next Generation)",
        "description_text": (
            "Bold.org-hosted scholarship for the next generation of veterans "
            "and military families pursuing higher education."
        ),
        "apply_url": "https://bold.org/scholarships/veterans-next-generation-scholarship/",
        "award_amount_min": 1000,
        "award_amount_max": 5000,
        "deadline_at": None,
        "rolling_deadline": True,
        "degree_level": "undergraduate",
        "field_of_study": "any",
        "geographic_restriction": "US",
        "eligibility_criteria": (
            "Vet or military-connected. Carlos qualifies. Bold.org requires "
            "account signup. needs_verification: confirm current cycle on "
            "Bold.org portal."
        ),
        "essay_required": True,
    },
]


def seed_curated() -> int:
    """Upsert all curated entries. Idempotent — safe to re-run.
    Returns count loaded."""
    n = 0
    for entry in _CURATED:
        try:
            upsert_posting(**entry)
            n += 1
        except Exception as e:
            print(f"[curated_seed] failed to upsert {entry.get('external_id')}: {e}")
    return n


if __name__ == "__main__":
    n = seed_curated()
    print(f"Seeded {n} curated scholarships.")
