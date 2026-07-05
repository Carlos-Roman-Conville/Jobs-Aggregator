"""Round 3 quality tests: years echo, project curation, skills sync, jargon."""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from job_pipeline.cover_letter_tailor import _normalize_cover_letter_content  # noqa: E402
from job_pipeline.named_requirements import (  # noqa: E402
    curate_projects,
    ensure_surfaced_keywords_in_skills,
    extract_jd_experience_bands,
    find_jd_years_echo_violations,
    find_project_jargon_violations,
    fix_jd_years_echo_in_text,
    years_experience_prompt_block,
)
from job_pipeline.resume_tailor import (  # noqa: E402
    _ensure_m365_for_support_role,
    _fix_jd_years_echo_summary,
    validate_tailored_content,
)


PROFILE_WITH_TENURE = """
IT Support Specialist with 3+ years of help desk experience.
Microsoft 365 and Google Workspace support for end users.
"""

JD_WITH_BAND = """
Help Desk Technician — 3-5 years of experience required.
Microsoft 365, Freshdesk, and user account support.
"""


class TestYearsExperienceEcho(unittest.TestCase):
    def test_extracts_jd_band(self):
        bands = extract_jd_experience_bands(JD_WITH_BAND)
        self.assertTrue(any("3-5" in b for b in bands))

    def test_flags_jd_band_echo_without_profile_backing(self):
        text = "I have 3-5 years of experience in IT support."
        violations = find_jd_years_echo_violations(text, JD_WITH_BAND, PROFILE_WITH_TENURE)
        self.assertTrue(violations)

    def test_allows_profile_backed_band(self):
        profile = "Candidate has 3-5 years of documented IT experience."
        text = "I bring 3-5 years of experience to this role."
        violations = find_jd_years_echo_violations(text, JD_WITH_BAND, profile)
        self.assertFalse(violations)

    def test_fix_replaces_with_profile_phrase(self):
        text = "Professional with 3-5 years in help desk roles."
        fixed, changed = fix_jd_years_echo_in_text(text, JD_WITH_BAND, PROFILE_WITH_TENURE)
        self.assertTrue(changed)
        self.assertIn("3+ years", fixed.lower())
        self.assertNotIn("3-5", fixed.lower())

    def test_prompt_block_lists_jd_bands(self):
        block = years_experience_prompt_block(JD_WITH_BAND, PROFILE_WITH_TENURE)
        self.assertIn("NEVER echo", block)
        self.assertIn("3-5", block)

    def test_validate_fixes_summary_echo(self):
        content = {
            "summary": "IT Support with 3-5 years of help desk experience.",
            "experience": [],
            "skills": {"technical": ["Freshdesk"], "soft": []},
            "projects": [],
        }
        result = validate_tailored_content(
            content,
            JD_WITH_BAND,
            PROFILE_WITH_TENURE,
            job_title="Help Desk Technician",
        )
        self.assertNotIn("3-5", content["summary"].lower())
        self.assertTrue(
            any("replaced JD experience-band" in i for i in result["issues"])
            or "3+ years" in content["summary"].lower()
        )


class TestProjectCuration(unittest.TestCase):
    def test_caps_projects_for_help_desk_role(self):
        content = {
            "projects": [
                {
                    "name": "AI Job-Application Pipeline",
                    "description": "Python automation for job discovery and workflow.",
                    "impact": "Support-style workflow improvements.",
                },
                {
                    "name": "The Organizer",
                    "description": "Personal productivity app.",
                    "impact": "Side project.",
                },
                {
                    "name": "Art Pipeline",
                    "description": "Creative asset pipeline tool.",
                    "impact": "Art workflow.",
                },
            ],
            "summary": "",
            "skills": {"technical": [], "soft": []},
        }
        jd = "Help desk role requiring Microsoft 365 and ticketing."
        curated, notes = curate_projects(
            content,
            jd,
            PROFILE_WITH_TENURE,
            job_title="Help Desk Technician",
        )
        self.assertLessEqual(len(curated), 2)
        names = [str(p.get("name") or "") for p in curated]
        self.assertNotIn("Art Pipeline", names)
        self.assertTrue(notes or len(curated) <= 2)

    def test_injects_m365_for_support_role_when_authorized(self):
        # career_master.md authorizes M365; LLM left it out of skills.technical on a
        # service-desk build. Injector should add "Microsoft 365" deterministically.
        content = {
            "skills": {
                "technical": ["Ticketing/ITSM", "Windows OS Troubleshooting", "Python"],
                "soft": [],
            }
        }
        issues = []
        _ensure_m365_for_support_role(content, "Service Desk Technician", issues)
        tech = [str(s).lower() for s in content["skills"]["technical"]]
        self.assertIn("microsoft 365", tech)
        self.assertTrue(any("injected Microsoft 365" in i for i in issues))

    def test_m365_inject_skipped_for_non_support_role(self):
        content = {"skills": {"technical": ["Python", "Django"], "soft": []}}
        issues = []
        _ensure_m365_for_support_role(content, "Senior Backend Engineer", issues)
        tech = [str(s).lower() for s in content["skills"]["technical"]]
        self.assertNotIn("microsoft 365", tech)
        self.assertEqual(issues, [])

    def test_light_exposure_rewrites_bare_canonical(self):
        from job_pipeline.named_requirements import (
            enforce_light_exposure_framing_on_skills,
        )
        # Profile authorizes only the "basics" framing for AD and VLAN.
        profile_text = """
## Light exposure

- **Active Directory basics**: gpedit.msc at BTB; planned home-lab build. Not GPMC admin.
- **VLAN basics**: web-UI VLAN config at single-site BTB. Not Cisco IOS.
- **VirtualBox (home lab)**: VM creation for personal labs. Not production hypervisor admin.
"""
        # LLM produced bare canonicals (overclaim shape from the Pure IT build).
        content = {
            "skills": {
                "technical": ["Active Directory", "VLAN", "VirtualBox", "DNS"],
                "soft": [],
            }
        }
        notes = enforce_light_exposure_framing_on_skills(content, profile_text)
        tech = content["skills"]["technical"]
        self.assertIn("Active Directory basics", tech)
        self.assertIn("VLAN basics", tech)
        self.assertIn("VirtualBox (home lab)", tech)
        # DNS is not in light-exposure list — leave alone.
        self.assertIn("DNS", tech)
        # Bare canonicals must be gone.
        self.assertNotIn("Active Directory", tech)
        self.assertNotIn("VLAN", tech)
        self.assertNotIn("VirtualBox", tech)
        self.assertEqual(len(notes), 3)

    def test_light_exposure_noop_when_already_qualified(self):
        from job_pipeline.named_requirements import (
            enforce_light_exposure_framing_on_skills,
        )
        profile_text = """
## Light exposure

- **Active Directory basics**: gpedit.msc only. Not GPMC.
"""
        content = {
            "skills": {
                "technical": ["Active Directory basics", "DNS"],
                "soft": [],
            }
        }
        notes = enforce_light_exposure_framing_on_skills(content, profile_text)
        # Already in approved framing — no rewrite.
        self.assertEqual(notes, [])
        self.assertEqual(content["skills"]["technical"], ["Active Directory basics", "DNS"])

    def test_m365_inject_skipped_when_already_present(self):
        content = {
            "skills": {
                "technical": ["Microsoft 365", "Ticketing/ITSM"],
                "soft": [],
            }
        }
        issues = []
        _ensure_m365_for_support_role(content, "Help Desk Technician", issues)
        # Still exactly one M365 entry; no spurious injection.
        m365_entries = [s for s in content["skills"]["technical"] if "365" in s.lower()]
        self.assertEqual(len(m365_entries), 1)
        self.assertEqual(issues, [])

    def test_organizer_hard_blocked_for_support_role_despite_token_overlap(self):
        # Logicalis JD had "workflow"/"support"/"tasks" terms that bumped the
        # Organizer's soft penalty above the -3.0 threshold. Hard-block must
        # fire regardless of token overlap.
        content = {
            "projects": [
                {
                    "name": "AI job-application pipeline",
                    "description": "Modular Python pipeline for job discovery, scoring, and tailoring.",
                    "impact": "Workflow design.",
                },
                {
                    "name": "The Organizer",
                    "description": (
                        "Automatic file-routing system watching the Downloads folder, "
                        "supporting recurring tasks and workflow improvements through automation."
                    ),
                    "impact": "Demonstrates a habit of organizing recurring support tasks.",
                },
            ]
        }
        jd = (
            "Service Desk Technician supporting tickets, knowledgebase navigation, "
            "ServiceNow, customer service, documentation, workflow management, "
            "remote access tools, and recurring support tasks at high volume."
        )
        curated, notes = curate_projects(
            content,
            jd,
            PROFILE_WITH_TENURE,
            job_title="Service Desk Technician (Tier 1)",
        )
        names = [p.get("name") for p in curated]
        self.assertIn("AI job-application pipeline", names)
        self.assertNotIn("The Organizer", names)
        self.assertTrue(any("dropped" in n for n in notes))

    def test_drops_organizer_even_when_under_cap(self):
        # The Sarnova/Digitech leak: LLM returned exactly 2 projects (AI pipeline +
        # Organizer). The previous fast-path skipped scoring whenever len <= cap,
        # so the support-role penalty never fired and Organizer survived.
        content = {
            "projects": [
                {
                    "name": "AI Job-Application Pipeline",
                    "description": "Python automation for job discovery and workflow.",
                    "impact": "Support-style workflow improvements.",
                },
                {
                    "name": "The Organizer",
                    "description": "Personal productivity app.",
                    "impact": "Side project.",
                },
            ],
            "summary": "",
            "skills": {"technical": [], "soft": []},
        }
        jd = "Service Desk Technician supporting ticketing, hardware, Windows endpoints."
        curated, notes = curate_projects(
            content,
            jd,
            PROFILE_WITH_TENURE,
            job_title="Service Desk Technician",
        )
        names = [str(p.get("name") or "") for p in curated]
        self.assertNotIn("The Organizer", names)
        self.assertIn("AI Job-Application Pipeline", names)
        self.assertTrue(any("dropped" in n for n in notes))


class TestSurfacedKeywordsInSkills(unittest.TestCase):
    def test_adds_m365_to_skills_when_in_summary_only(self):
        content = {
            "summary": "Help desk support with Microsoft 365 and Freshdesk experience.",
            "experience": [],
            "skills": {"technical": ["Freshdesk", "Linux"], "soft": []},
            "projects": [],
        }
        jd = "Microsoft 365 and Freshdesk help desk support."
        notes = ensure_surfaced_keywords_in_skills(content, jd, PROFILE_WITH_TENURE)
        tech = [t.lower() for t in content["skills"]["technical"]]
        self.assertIn("microsoft 365", tech)
        self.assertTrue(notes)

    def test_adds_m365_to_skills_when_in_experience_bullet_only(self):
        content = {
            "summary": "Help desk support specialist.",
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": ["Supported users in Microsoft 365 environments."],
                }
            ],
            "skills": {"technical": ["Freshdesk", "Linux"], "soft": []},
            "projects": [],
        }
        jd = "Microsoft 365 and Freshdesk help desk support."
        notes = ensure_surfaced_keywords_in_skills(content, jd, PROFILE_WITH_TENURE)
        tech = [t.lower() for t in content["skills"]["technical"]]
        self.assertIn("microsoft 365", tech)
        self.assertTrue(notes)


class TestProjectJargon(unittest.TestCase):
    def test_detects_architectural_pivot(self):
        hits = find_project_jargon_violations(
            "My architectural pivot in the AI job-application pipeline project."
        )
        self.assertIn("architectural pivot", hits)

    def test_cover_letter_warns_on_jargon(self):
        out = _normalize_cover_letter_content(
            {
                "opening": "Hello.",
                "body_paragraphs": [
                    "An architectural pivot in my personal pipeline shows my skills."
                ],
                "closing": "Thanks.",
            },
            job_description="Help desk role.",
            profile_text=PROFILE_WITH_TENURE,
        )
        self.assertEqual(out["opening"], "Hello.")
        self.assertEqual(len(out["body_paragraphs"]), 1)
        self.assertEqual(out["closing"], "Thanks.")
        warnings_blob = " ".join(out.get("_cl_warnings") or [])
        self.assertIn("project_jargon", warnings_blob)
        self.assertIn("architectural pivot", warnings_blob)

    def test_cover_letter_fixes_jd_years_echo(self):
        out = _normalize_cover_letter_content(
            {
                "opening": "Dear team,",
                "body_paragraphs": ["I have 3-5 years of help desk experience."],
                "closing": "Thank you.",
            },
            job_description=JD_WITH_BAND,
            profile_text=PROFILE_WITH_TENURE,
        )
        self.assertEqual(out["opening"], "Dear team,")
        self.assertEqual(out["closing"], "Thank you.")
        body = " ".join(out.get("body_paragraphs") or [])
        self.assertNotIn("3-5 years", body.lower())
        self.assertIn("3+ years", body.lower())


if __name__ == "__main__":
    unittest.main()
