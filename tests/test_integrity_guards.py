"""Phase 0 integrity guard tests — credibility-critical, always-on, no LLM."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_pipeline import integrity_guards, evidence_db


_FIXTURE_EVIDENCE = {
    "employers": {
        "BEAT_THE_BOMB": {
            "display_name": "BEAT THE BOMB",
            "aliases": ["beat the bomb", "beat-the-bomb"],
            "metrics": [
                "reduced guest transition wait time 75% (10 min to 2.5 min)",
                "supported 20-30 staff per shift",
            ],
            "metric_display": [
                "Reduced guest transition wait time by 75%, from approximately 10 minutes to 2.5 minutes, by improving workflow and handoff processes.",
                "Supported 20–30 staff per shift while maintaining clear communication during live technical incidents.",
            ],
        },
        "ACROPOLIS_MOTOR_INN": {
            "display_name": "Acropolis Motor Inn",
            "aliases": ["newport beach investments", "acropolis"],
            "metrics": ["managed seasonal guest bookings"],
        },
        "GOT_JUNK": {
            "display_name": "1-800-GOT-JUNK",
            "aliases": ["1-800-got-junk", "got-junk"],
            "metrics": ["reduced lost-job rates by approximately 15%"],
        },
    },
    "global_metrics": [],
}


class _FixtureEvidenceMixin:
    """Swap in a controlled evidence.json for each test class."""

    @classmethod
    def setUpClass(cls):  # type: ignore[override]
        cls._tmp = tempfile.TemporaryDirectory()
        cls._tmp_path = Path(cls._tmp.name) / "evidence.json"
        cls._tmp_path.write_text(json.dumps(_FIXTURE_EVIDENCE), encoding="utf-8")
        cls._env = mock.patch.dict(
            os.environ, {"JOB_PIPELINE_EVIDENCE_PATH": str(cls._tmp_path)}
        )
        cls._env.start()
        evidence_db.clear_evidence_cache()

    @classmethod
    def tearDownClass(cls):  # type: ignore[override]
        cls._env.stop()
        evidence_db.clear_evidence_cache()
        cls._tmp.cleanup()


class TestCrossJobMetricLeak(_FixtureEvidenceMixin, unittest.TestCase):
    """0.1 — A metric must never appear under an employer that doesn't own it."""

    def test_strips_btb_metric_leaked_into_hotel_job(self):
        content = {
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Reduced guest transition wait time 75% (10 min to 2.5 min)",
                        "Supported 20-30 staff per shift",
                    ],
                },
                {
                    "company": "Newport Beach Investments / Acropolis Motor Inn",
                    "bullets": [
                        "Reduced guest transition wait time 75% (10 min to 2.5 min)",
                        "Supported 20-30 staff per shift on hotel front desk",
                        "Managed seasonal guest bookings and reservations",
                    ],
                },
            ]
        }
        notes = integrity_guards.strip_cross_job_metric_leaks(content)

        btb_bullets = content["experience"][0]["bullets"]
        hotel_bullets = content["experience"][1]["bullets"]

        # BEAT THE BOMB keeps its own metrics
        self.assertTrue(any("75%" in b for b in btb_bullets))
        self.assertTrue(any("20-30" in b for b in btb_bullets))

        # Hotel job loses BEAT THE BOMB's metrics, keeps its own
        self.assertFalse(any("75%" in b for b in hotel_bullets))
        self.assertFalse(any("20-30" in b for b in hotel_bullets))
        self.assertTrue(any("seasonal guest bookings" in b for b in hotel_bullets))

        # Notes describe what was removed
        self.assertTrue(any("stripped" in n for n in notes))

    def test_unknown_employer_with_no_leak_untouched(self):
        content = {
            "experience": [
                {
                    "company": "Some Other Unknown Co",
                    "bullets": ["Greeted customers", "Answered phones"],
                }
            ]
        }
        notes = integrity_guards.strip_cross_job_metric_leaks(content)
        self.assertEqual(content["experience"][0]["bullets"], ["Greeted customers", "Answered phones"])
        self.assertEqual(notes, [])


class TestMalformedSummary(_FixtureEvidenceMixin, unittest.TestCase):
    """0.2 — Catch AI comma-salad summaries."""

    def test_catches_thesis_ghost_sentence(self):
        bad = (
            "Help desk candidate who troubleshoots under pressure, communicates "
            "clearly, documents fixes, and improves workflows — aligned with mission, "
            "communication, customer while delivering supported experience in "
            "Ticketing / ITSM, PST / time-zone coverage."
        )
        self.assertTrue(integrity_guards.is_malformed_line(bad))

    def test_strips_thesis_ghost_keeps_rest(self):
        content = {
            "summary": (
                "Service Desk Technician with three years of hands-on user support. "
                "Aligned with mission, communication, customer while delivering "
                "supported experience in Ticketing / ITSM, PST / time-zone coverage."
            )
        }
        notes = integrity_guards.clean_summary(content)
        self.assertTrue(notes)
        self.assertIn("Service Desk Technician", content["summary"])
        self.assertNotIn("aligned with mission", content["summary"].lower())
        self.assertNotIn("PST / time-zone", content["summary"])

    def test_clean_summary_no_op_on_good_summary(self):
        good = (
            "Service Desk Technician with three years of help-desk experience supporting "
            "Windows endpoints, ticketing workflows, and onboarding documentation."
        )
        content = {"summary": good}
        notes = integrity_guards.clean_summary(content)
        self.assertEqual(notes, [])
        self.assertEqual(content["summary"], good)


class TestSkillsSemanticDedupe(_FixtureEvidenceMixin, unittest.TestCase):
    """0.3 — Semantic concept dedupe."""

    def test_merges_ticketing_variants(self):
        items = ["Ticketing / ITSM", "ticketing", "Help desk ticketing", "ITSM"]
        out, notes = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(out, ["Ticketing/ITSM"])
        self.assertTrue(notes)

    def test_merges_microsoft_365_variants(self):
        items = ["Microsoft 365", "M365", "Office 365", "o365"]
        out, _ = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(out, ["Microsoft 365"])

    def test_preserves_unknown_distinct_skills(self):
        items = ["Windows OS troubleshooting", "Dante audio", "RFID"]
        out, _ = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(out, ["Windows OS Troubleshooting", "Dante audio", "RFID"])

    def test_orders_preserved(self):
        items = ["Active Directory", "Ticketing", "MFA"]
        out, _ = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(out[0], "Active Directory")
        self.assertEqual(out[1], "Ticketing/ITSM")
        self.assertEqual(out[2], "MFA/SSO")

    def test_merges_sop_runbook_variants(self):
        """Regression: "SOP and runbook authoring" + "runbooks" must collapse to one."""
        items = [
            "SOP and runbook authoring",
            "runbooks",
            "SOPs",
            "runbook",
            "SOP authoring",
            "SOP/runbook authoring",
        ]
        out, notes = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(out, ["SOPs/Runbooks"])
        self.assertTrue(notes)

    def test_sop_runbook_dedupe_inside_realistic_skills_line(self):
        items = [
            "Ticketing/ITSM",
            "Windows OS troubleshooting",
            "SOP and runbook authoring",
            "DNS troubleshooting",
            "runbooks",
            "TCP/IP",
        ]
        out, _ = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(
            out,
            [
                "Ticketing/ITSM",
                "Windows OS Troubleshooting",
                "SOPs/Runbooks",
                "DNS troubleshooting",
                "TCP/IP",
            ],
        )

    def test_canonicalize_skill_normalizes_whitespace_and_slashes(self):
        self.assertEqual(integrity_guards.canonicalize_skill("TCP / IP"), "TCP/IP")
        self.assertEqual(integrity_guards.canonicalize_skill("MFA / SSO"), "MFA/SSO")
        self.assertEqual(
            integrity_guards.canonicalize_skill("Ticketing  /  ITSM"), "Ticketing/ITSM"
        )

    def test_canonicalize_skill_handles_pluralization(self):
        self.assertEqual(integrity_guards.canonicalize_skill("SOPs"), "SOPs/Runbooks")
        self.assertEqual(integrity_guards.canonicalize_skill("runbooks"), "SOPs/Runbooks")


class TestNonSkillBlocklist(_FixtureEvidenceMixin, unittest.TestCase):
    """0.3 — Availability / logistics terms must NEVER appear in the skills list."""

    def test_removes_pst_and_timezone_variants(self):
        """Regression: 'PST' and 'PST / time-zone coverage' must be removed entirely."""
        items = [
            "Ticketing/ITSM",
            "PST",
            "PST / time-zone coverage",
            "time-zone coverage",
            "Windows OS troubleshooting",
        ]
        out, notes = integrity_guards.dedupe_skills_semantic(items)
        self.assertEqual(out, ["Ticketing/ITSM", "Windows OS Troubleshooting"])
        # All three removals logged
        removed = [n for n in notes if "removed non-skill item" in n]
        self.assertEqual(len(removed), 3)
        joined = " ".join(removed).lower()
        self.assertIn("pst", joined)
        self.assertIn("time-zone coverage", joined)

    def test_is_non_skill_item_case_and_slash_tolerant(self):
        self.assertTrue(integrity_guards.is_non_skill_item("PST"))
        self.assertTrue(integrity_guards.is_non_skill_item("pst"))
        self.assertTrue(integrity_guards.is_non_skill_item("PST / time-zone coverage"))
        self.assertTrue(integrity_guards.is_non_skill_item("pst/time-zone coverage"))
        self.assertTrue(integrity_guards.is_non_skill_item("Time-Zone Coverage"))
        self.assertTrue(integrity_guards.is_non_skill_item("on-call availability"))
        self.assertTrue(integrity_guards.is_non_skill_item("weekend availability"))

    def test_real_skill_named_pst_not_falsely_blocked(self):
        # Skill items that legitimately contain a region but ARE skills (e.g.
        # "PowerShell scripting" — starts with "P" but unrelated to PST).
        self.assertFalse(integrity_guards.is_non_skill_item("PowerShell scripting"))
        self.assertFalse(integrity_guards.is_non_skill_item("Postman API testing"))
        # Whole-word match — "pst" inside another word stays a skill.
        self.assertFalse(integrity_guards.is_non_skill_item("Windows OS troubleshooting"))

    def test_clean_skill_items_path_also_strips_blocklist(self):
        """The bypass path through clean_skill_items must also strip PST."""
        from job_pipeline.rendercv_export import clean_skill_items

        out = clean_skill_items(
            [
                "Ticketing/ITSM",
                "PST",
                "PST / time-zone coverage",
                "SOPs",
                "runbooks",
            ]
        )
        self.assertEqual(out, ["Ticketing/ITSM", "SOPs/Runbooks"])


class TestIntraJobBulletDedupe(_FixtureEvidenceMixin, unittest.TestCase):
    """0.4 — Collapse near-duplicate bullets within one job."""

    def test_collapses_supported_vs_supervised_20_30(self):
        content = {
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Supported 20-30 staff per shift",
                        "Supervised 20-30 team members on shift",
                        "Documented onboarding SOPs",
                    ],
                }
            ]
        }
        notes = integrity_guards.dedupe_intra_job_bullets(content)
        bullets = content["experience"][0]["bullets"]
        self.assertEqual(len(bullets), 2)
        # one of the 20-30 dupes survives
        self.assertEqual(sum(1 for b in bullets if "20-30" in b), 1)
        self.assertIn("Documented onboarding SOPs", bullets)
        self.assertTrue(notes)

    def test_distinct_bullets_untouched(self):
        content = {
            "experience": [
                {
                    "company": "X",
                    "bullets": [
                        "Resolved Windows endpoint issues over remote sessions",
                        "Authored runbooks for new-hire onboarding",
                    ],
                }
            ]
        }
        before = list(content["experience"][0]["bullets"])
        integrity_guards.dedupe_intra_job_bullets(content)
        self.assertEqual(content["experience"][0]["bullets"], before)


class TestRunAllGuards(_FixtureEvidenceMixin, unittest.TestCase):
    """End-to-end: a single pass mutates content as expected and returns notes."""

    def test_run_integrity_guards_full_pass(self):
        content = {
            "summary": (
                "Help desk candidate. Aligned with mission, communication, customer "
                "while delivering supported experience in Ticketing / ITSM, PST / "
                "time-zone coverage."
            ),
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Supported 20-30 staff per shift",
                        "Supervised 20-30 team members on shift",
                    ],
                },
                {
                    "company": "Newport Beach Investments / Acropolis Motor Inn",
                    "bullets": [
                        "Reduced guest transition wait time 75% (10 min to 2.5 min)",
                        "Managed seasonal guest bookings",
                    ],
                },
            ],
            "skills": {
                "technical": [
                    "Ticketing / ITSM",
                    "ticketing",
                    "ITSM",
                    "Windows OS troubleshooting",
                ],
                "soft": ["Clear communication", "clear communication"],
            },
        }
        notes = integrity_guards.run_integrity_guards(content)
        self.assertTrue(notes)

        # Summary cleaned
        self.assertNotIn("aligned with mission", content["summary"].lower())

        # Cross-job leak removed from hotel job
        hotel_bullets = content["experience"][1]["bullets"]
        self.assertFalse(any("75%" in b for b in hotel_bullets))

        # Intra-job dupe collapsed under BEAT THE BOMB (polished bullets use en-dash)
        btb_bullets = content["experience"][0]["bullets"]
        self.assertEqual(sum(1 for b in btb_bullets if "20" in b and "30" in b), 1)

        # Skills merged
        self.assertEqual(
            content["skills"]["technical"],
            ["Ticketing/ITSM", "Windows OS Troubleshooting"],
        )
        self.assertEqual(content["skills"]["soft"], ["Clear communication"])

    def test_idempotent(self):
        content = {
            "summary": (
                "Help desk technician with documented Windows endpoint support, "
                "Microsoft 365 troubleshooting, and ticketing workflows. "
                "Brings hands-on end-user support experience with clear documentation habits."
            ),
            "experience": [{"company": "BEAT THE BOMB", "bullets": ["Resolved tickets"]}],
            "skills": {"technical": ["Ticketing/ITSM"], "soft": []},
        }
        notes_1 = integrity_guards.run_integrity_guards(content)
        snapshot = json.dumps(content, sort_keys=True)
        notes_2 = integrity_guards.run_integrity_guards(content)
        self.assertEqual(snapshot, json.dumps(content, sort_keys=True))
        self.assertEqual(notes_2, [])
        # first pass may produce one or zero notes depending on input cleanliness
        self.assertIsInstance(notes_1, list)

    def test_error_content_skipped(self):
        content = {"error": "json_parse_failed"}
        notes = integrity_guards.run_integrity_guards(content)
        self.assertEqual(notes, [])
        self.assertEqual(content, {"error": "json_parse_failed"})


class TestRoleThesisGrammar(unittest.TestCase):
    """Regression: build_role_thesis must not produce comma-salad summaries."""

    def test_thesis_grammatical_with_abstract_culture_words(self):
        from job_pipeline.jd_analysis import build_role_thesis

        thesis = build_role_thesis(
            "IT Help Desk Specialist",
            {
                "culture_words": ["mission", "communication", "customer"],
                "technical_requirements": ["Ticketing / ITSM", "PST / time-zone coverage"],
            },
        )
        # Must not contain the old broken pattern
        self.assertNotIn(", communication, customer", thesis.lower())
        self.assertNotIn("aligned with", thesis.lower())
        self.assertNotIn("PST", thesis)
        # Should remain a single grammatical sentence
        self.assertTrue(thesis.endswith("."))


class TestMetricInjectionTruthGate(_FixtureEvidenceMixin, unittest.TestCase):
    """0.1 backstop: inject_metric_bank must NOT add metrics to unknown employers."""

    def test_unknown_employer_gets_no_metric(self):
        from job_pipeline import resume_optimizer

        content = {
            "experience": [
                {
                    "company": "Some Company Not In Evidence DB",
                    "bullets": ["Did stuff"],
                }
            ]
        }
        notes = resume_optimizer.inject_metric_bank(content)
        self.assertEqual(content["experience"][0]["bullets"], ["Did stuff"])
        self.assertTrue(any("skipped metric injection" in n for n in notes))

    def test_known_employer_gets_own_metric(self):
        from job_pipeline import resume_optimizer

        content = {
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": ["Did stuff"],
                }
            ]
        }
        notes = resume_optimizer.inject_metric_bank(content)
        bullets = content["experience"][0]["bullets"]
        joined = " ".join(bullets).lower()
        self.assertTrue("75%" in joined or "20-30" in joined)
        self.assertTrue(any("injected metric" in n for n in notes))


class TestClaimAuditStrip(_FixtureEvidenceMixin, unittest.TestCase):
    def test_strips_defensive_summary_sentence(self):
        content = {
            "summary": (
                "Service Desk Technician with desktop support experience. "
                "Ticketing / ITSM is supported by managed help desk requests and documentation work; "
                "PST / time-zone coverage is not claimed."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        notes = integrity_guards.strip_claim_audit_from_resume(content)
        self.assertTrue(notes)
        self.assertNotIn("not claimed", content["summary"].lower())
        self.assertIn("Service Desk Technician", content["summary"])

    def test_strips_supported_through_phrasing(self):
        # Leaked from a Sarnova/Digitech build — "is supported through" was the
        # exact audit-mode phrasing the LLM was told to omit but echoed anyway.
        content = {
            "summary": (
                "Service Desk Technician candidate with hands-on hardware support. "
                "Ticketing / ITSM is supported through managed help desk requests and documentation work. "
                "Brings practical troubleshooting discipline."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        integrity_guards.strip_claim_audit_from_resume(content)
        self.assertNotIn("supported through", content["summary"].lower())
        self.assertIn("Service Desk Technician candidate", content["summary"])
        self.assertIn("practical troubleshooting", content["summary"])

    def test_strips_first_person_partial_hedge(self):
        content = {
            "summary": (
                "Tech ops candidate with hardware and Windows support. "
                "I have partial user-account support experience from onboarding workflows. "
                "Documented SOPs and runbooks for recurring issues."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        integrity_guards.strip_claim_audit_from_resume(content)
        self.assertNotIn("I have partial", content["summary"])
        self.assertIn("Tech ops candidate", content["summary"])
        self.assertIn("SOPs and runbooks", content["summary"])

    def test_strips_first_person_do_not_claim(self):
        content = {
            "summary": (
                "Service desk candidate with ticketing and documentation experience. "
                "I do not claim account administration or provisioning. "
                "Strong communicator during live incidents."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        integrity_guards.strip_claim_audit_from_resume(content)
        s = content["summary"]
        self.assertNotIn("do not claim", s.lower())
        self.assertNotIn("account administration or provisioning", s)
        self.assertIn("Service desk candidate", s)
        self.assertIn("live incidents", s)

    def test_strips_python_self_promo_for_support_role(self):
        # Sarnova/Digitech leak: LLM recruiter pass keeps resurfacing
        # "Supported by N years of self-taught Python on personal projects" in summary.
        content = {
            "summary": (
                "Service Desk Technician candidate with hands-on hardware and Windows support. "
                "Supported by 3 years of self-taught Python on personal projects. "
                "Brings practical workflow-improvement habits from technical operations."
            ),
            "experience": [],
            "skills": {"technical": ["Python"], "soft": []},
            "projects": [{"name": "AI Job-Application Pipeline", "description": "Python automation."}],
        }
        notes = integrity_guards.strip_python_self_promo_from_summary(
            content, "Service Desk Technician"
        )
        s = content["summary"]
        self.assertNotIn("self-taught Python", s)
        self.assertNotIn("years of self-taught", s)
        self.assertIn("Service Desk Technician candidate", s)
        self.assertIn("workflow-improvement habits", s)
        # Python is still allowed in skills and projects.
        self.assertIn("Python", content["skills"]["technical"])
        self.assertEqual(content["projects"][0]["name"], "AI Job-Application Pipeline")
        self.assertTrue(notes)

    def test_python_self_promo_strip_skips_non_support_roles(self):
        content = {
            "summary": (
                "Junior Backend Engineer with Python service experience. "
                "Supported by 3 years of self-taught Python on personal projects."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        notes = integrity_guards.strip_python_self_promo_from_summary(
            content, "Junior Backend Engineer"
        )
        # Non-support role: leave Python framing alone.
        self.assertIn("self-taught Python", content["summary"])
        self.assertEqual(notes, [])

    def test_run_integrity_guards_strips_supported_x_support_doubleup(self):
        # Always-on guarantee: even when anti_fluff (opt-in path) is skipped,
        # the "Supported X support" double-up is dropped before export.
        content = {
            "summary": "",
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Supported user account support, onboarding workflows, and access-related troubleshooting through front-line operational help.",
                        "Provided end-user mobile support for iOS/Android devices.",  # legitimate — must survive
                    ],
                }
            ],
            "skills": {"technical": [], "soft": []},
        }
        notes = integrity_guards.run_integrity_guards(content)
        bullets = content["experience"][0]["bullets"]
        self.assertIn("Supported user account, onboarding workflows", bullets[0])
        self.assertNotIn("account support,", bullets[0])
        # Legitimate phrasing untouched.
        self.assertEqual(
            bullets[1],
            "Provided end-user mobile support for iOS/Android devices.",
        )
        self.assertTrue(any("Supported X support" in n for n in notes))

    def test_run_integrity_guards_strips_python_for_support_title(self):
        # End-to-end: orchestrator should fire the python-self-promo guard
        # when job_title is a support role.
        content = {
            "summary": (
                "Service Desk Technician candidate with desktop support. "
                "Supported by 3 years of self-directed Python practice on personal projects."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        notes = integrity_guards.run_integrity_guards(
            content, job_title="Service Desk Technician"
        )
        self.assertNotIn("self-directed Python", content["summary"])
        self.assertTrue(any("Python self-promo" in n for n in notes))

    def test_strips_ad_gpedit_equivalence_from_summary(self):
        # Logicalis leak: LLM dumped the full Light Exposure framing as prose
        # into the summary, equating Local Group Policy with Active Directory.
        content = {
            "summary": (
                "Service Desk Technician with experience. "
                "Brings Active Directory basics via Local Group Policy Editor "
                "(gpedit.msc) for per-machine Windows Update scheduling, plus "
                "practical ticketing habits."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        notes = integrity_guards.strip_ad_gpedit_equivalence(content)
        s = content["summary"]
        self.assertNotIn("Active Directory basics via", s)
        self.assertNotIn("gpedit", s)
        self.assertNotIn("Local Group Policy", s)
        self.assertIn("Service Desk Technician with experience", s)
        self.assertIn("practical ticketing habits", s)
        self.assertTrue(notes)

    def test_ad_gpedit_preserves_legitimate_ad_claim(self):
        # Real AD usage (no gpedit equivalence) must survive untouched.
        original = "Used Active Directory at scale with 200+ users daily."
        content = {"summary": original, "experience": [], "skills": {}}
        notes = integrity_guards.strip_ad_gpedit_equivalence(content)
        self.assertEqual(content["summary"], original)
        self.assertEqual(notes, [])

    def test_replaces_duplicated_project_impact(self):
        # U.S. Courts leak: project impact was a verbatim copy of description.
        content = {
            "summary": "",
            "experience": [],
            "skills": {"technical": [], "soft": []},
            "projects": [
                {
                    "name": "AI Job-Application Pipeline",
                    "description": (
                        "Built a modular Python-based job-application pipeline for job "
                        "discovery, scoring, resume tailoring, and application tracking."
                    ),
                    "impact": (
                        "Built a modular Python-based job-application pipeline for job "
                        "discovery, scoring, resume tailoring, and application tracking."
                    ),
                }
            ],
        }
        notes = integrity_guards.dedupe_project_description_impact(content)
        impact = content["projects"][0]["impact"]
        self.assertNotIn("Built a modular Python-based", impact)
        self.assertIn("Demonstrates", impact)
        self.assertTrue(notes)

    def test_does_not_replace_when_impact_differs(self):
        content = {
            "projects": [
                {
                    "name": "Project X",
                    "description": "Built a Python pipeline for X.",
                    "impact": "Reduced manual triage time by 40%.",
                }
            ]
        }
        before = content["projects"][0]["impact"]
        integrity_guards.dedupe_project_description_impact(content)
        self.assertEqual(content["projects"][0]["impact"], before)

    def test_strips_duplicate_phrase_in_summary(self):
        # Arize leak: "account creation/disable workflows" appeared twice
        # within one sentence in the summary.
        content = {
            "summary": (
                "IT Support Specialist with hands-on end-user support. "
                "Supported user account management through account creation/disable workflows, "
                "access-related troubleshooting, onboarding documentation, "
                "and account creation/disable workflows at small-shop scale."
            ),
            "experience": [],
            "skills": {"technical": [], "soft": []},
        }
        notes = integrity_guards.strip_duplicate_phrases_from_summary(content)
        s = content["summary"]
        # Phrase appears only once now.
        self.assertEqual(s.lower().count("account creation/disable workflows"), 1)
        # Other content survives.
        self.assertIn("access-related troubleshooting", s)
        self.assertIn("onboarding documentation", s)
        self.assertIn("small-shop scale", s)
        self.assertTrue(notes)

    def test_no_phrase_dedupe_for_short_repeats(self):
        # Tool names appearing twice (e.g. "Microsoft 365" — only 2 words)
        # must not be stripped — min_words threshold is 3.
        content = {
            "summary": (
                "Used Microsoft 365 daily at BTB. "
                "Microsoft 365 was core to operations."
            )
        }
        notes = integrity_guards.strip_duplicate_phrases_from_summary(content)
        self.assertEqual(notes, [])
        self.assertEqual(content["summary"].lower().count("microsoft 365"), 2)

    def test_strips_personal_project_only_disclaimer(self):
        # Logicalis leak: project description started with "Personal project only;"
        # which reads as defensive on a resume.
        content = {
            "summary": "",
            "experience": [],
            "skills": {"technical": [], "soft": []},
            "projects": [
                {
                    "name": "AI job-application pipeline",
                    "description": "Built a modular Python pipeline. Personal project only; used as workflow practice.",
                    "impact": "Side project only, shows documentation habits.",
                }
            ],
        }
        integrity_guards.strip_claim_audit_from_resume(content)
        desc = content["projects"][0]["description"]
        impact = content["projects"][0]["impact"]
        self.assertNotIn("Personal project only", desc)
        self.assertNotIn("Side project only", impact)
        self.assertIn("Built a modular Python pipeline", desc)
        self.assertIn("workflow practice", desc)
        self.assertIn("documentation habits", impact)

    def test_reorder_btb_first_for_pure_helpdesk(self):
        content = {
            "experience": [
                {"company": "1-800-GOT-JUNK", "position": "Coordinator"},
                {"company": "BEAT THE BOMB", "position": "Tech Ops"},
            ]
        }
        notes = integrity_guards.reorder_experience_by_role_family(
            content, "Service Desk Technician Tier 1"
        )
        self.assertEqual(content["experience"][0]["company"], "BEAT THE BOMB")
        self.assertTrue(notes)

    def test_reorder_gotjunk_first_for_admin_hybrid(self):
        content = {
            "experience": [
                {"company": "BEAT THE BOMB", "position": "Tech Ops"},
                {"company": "1-800-GOT-JUNK", "position": "Coordinator"},
            ]
        }
        notes = integrity_guards.reorder_experience_by_role_family(
            content, "Administrative Assistant / Helpdesk Technician"
        )
        self.assertEqual(content["experience"][0]["company"], "1-800-GOT-JUNK")
        self.assertTrue(notes)

    def test_reorder_noop_when_already_correct(self):
        content = {
            "experience": [
                {"company": "BEAT THE BOMB", "position": "Tech Ops"},
                {"company": "1-800-GOT-JUNK", "position": "Coordinator"},
            ]
        }
        notes = integrity_guards.reorder_experience_by_role_family(
            content, "Service Desk Technician"
        )
        self.assertEqual(notes, [])

    def test_fuzzy_dedupe_collapses_token_subset(self):
        # Logicalis bilingual leak: "Microsoft 365" + "Microsoft 365 suite"
        # both survived because the canonical map didn't include "suite".
        # Now caught either by canonical map (preferred) OR fuzzy subset
        # (backstop for variants we haven't pre-listed).
        skills = ["Microsoft 365 suite", "Microsoft 365", "Ticketing/ITSM"]
        deduped, notes = integrity_guards.dedupe_skills_semantic(skills)
        self.assertIn("Microsoft 365", deduped)
        self.assertNotIn("Microsoft 365 suite", deduped)
        self.assertIn("Ticketing/ITSM", deduped)
        # Either dedupe mechanism is acceptable.
        self.assertTrue(
            any("fuzzy-dropped" in n or "merged skill variant" in n or "dropped duplicate" in n for n in notes)
        )

        # Also test a NOVEL variant the canonical map doesn't know about,
        # to verify the fuzzy subset pass still fires as the backstop.
        novel = ["Custom Tool", "Custom Tool advanced module"]
        deduped2, notes2 = integrity_guards.dedupe_skills_semantic(novel)
        self.assertEqual(deduped2, ["Custom Tool"])
        self.assertTrue(any("fuzzy-dropped" in n for n in notes2))

    def test_fuzzy_dedupe_preserves_distinct_skills(self):
        # "Linux server troubleshooting" and "Windows server troubleshooting"
        # share two tokens but neither is a subset of the other — keep both.
        skills = ["Linux server troubleshooting", "Windows server troubleshooting"]
        deduped, _ = integrity_guards.dedupe_skills_semantic(skills)
        self.assertEqual(len(deduped), 2)
        self.assertIn("Linux server troubleshooting", deduped)
        self.assertIn("Windows server troubleshooting", deduped)

    def test_cross_doc_strips_shared_narrative_phrase(self):
        # "small-shop environment" appears in both resume and cover letter —
        # strip from cover letter, keep in resume.
        resume = {
            "summary": "Service Desk Technician in a small-shop environment.",
            "experience": [],
        }
        cl = {
            "opening": "I am applying.",
            "body_paragraphs": [
                "My BTB work was in a small-shop environment with daily tickets.",
                "Microsoft 365 and RustDesk experience also relevant.",
            ],
            "closing": "Thanks.",
        }
        notes = integrity_guards.strip_phrases_shared_with_resume(cl, resume)
        # The narrative phrase is gone from CL body.
        self.assertNotIn("small-shop environment", cl["body_paragraphs"][0])
        # Tool names untouched.
        self.assertIn("Microsoft 365", cl["body_paragraphs"][1])
        self.assertIn("RustDesk", cl["body_paragraphs"][1])
        # Resume still has the phrase.
        self.assertIn("small-shop environment", resume["summary"])
        self.assertTrue(notes)

    def test_cross_doc_dedupe_skips_when_no_resume(self):
        cl = {"opening": "x", "body_paragraphs": ["y"], "closing": "z"}
        notes = integrity_guards.strip_phrases_shared_with_resume(cl, None)
        self.assertEqual(notes, [])

    def test_preserves_dot_prefixed_tokens_in_bullets(self):
        # Sarnova/Digitech leak: "and .env/JSON configuration management" was being
        # collapsed to "and.env/JSON" by the orphan-period regex.
        content = {
            "summary": "",
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Tuned services using shell scripts and .env/JSON configuration management.",
                        "Restored config from .json backups and verified .py automation scripts.",
                    ],
                }
            ],
            "skills": {"technical": [], "soft": []},
        }
        integrity_guards.strip_claim_audit_from_resume(content)
        bullets = content["experience"][0]["bullets"]
        self.assertIn(".env", bullets[0])
        self.assertNotIn("and.env", bullets[0])
        self.assertIn(".json", bullets[1])
        self.assertIn(".py", bullets[1])

    def test_polishes_raw_metric_bullets(self):
        content = {
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Reduced guest transition wait time 75% (10 min to 2.5 min)",
                        "Supported 20-30 staff per shift",
                    ],
                }
            ]
        }
        notes = integrity_guards.polish_evidence_metric_bullets(content)
        bullets = content["experience"][0]["bullets"]
        self.assertTrue(notes)
        self.assertIn("by 75%", bullets[0])
        self.assertIn("handoff processes", bullets[0])
        self.assertIn("live technical incidents", bullets[1])


class TestCoverLetterGuards(unittest.TestCase):
    def test_drops_pipeline_tangent_for_help_desk(self):
        content = {
            "opening": "I am interested in the service desk role.",
            "body_paragraphs": [
                "I supported Windows endpoints and ticketing workflows.",
                "I build personal automation tools, including a Python job-application pipeline.",
            ],
            "closing": "Thank you.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content,
            job_title="Service Desk Technician",
            company="Digitech",
        )
        self.assertEqual(len(content["body_paragraphs"]), 1)
        self.assertTrue(any("pipeline tangent" in n for n in notes))

    def test_reorders_gotjunk_after_btb_in_body(self):
        content = {
            "opening": "Digitech needs steady front-line support.",
            "body_paragraphs": [
                "At 1-800-GOT-JUNK I managed customer communication and scheduling through Salesforce.",
                "At BEAT THE BOMB I managed help desk requests and wrote SOPs and runbooks.",
            ],
            "closing": "Thank you.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content,
            job_title="Service Desk Technician - Digitech - Remote",
            company="Sarnova",
        )
        self.assertIn("BEAT THE BOMB", content["body_paragraphs"][0])
        self.assertIn("GOT-JUNK", content["body_paragraphs"][1])
        self.assertTrue(any("BTB evidence before GOT-JUNK" in n for n in notes))

    def test_swaps_opening_when_gotjunk_leads(self):
        content = {
            "opening": "At 1-800-GOT-JUNK I managed customer communication through Salesforce.",
            "body_paragraphs": [
                "At BEAT THE BOMB I managed help desk requests and remote support with RustDesk.",
            ],
            "closing": "Thank you.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content,
            job_title="Service Desk Technician",
            company="Digitech",
        )
        self.assertIn("BEAT THE BOMB", content["opening"])
        self.assertIn("GOT-JUNK", content["body_paragraphs"][0])
        self.assertTrue(any("GOT-JUNK was leading" in n for n in notes))

    def test_strips_duplicate_closing_intent_in_body(self):
        # Arize leak: last body paragraph ended with "I'd welcome a conversation..."
        # and the closing field had "I'd like to discuss... Thank you..."
        content = {
            "opening": "Arize AI needs strong support.",
            "body_paragraphs": [
                "At BTB I handled help desk requests.",
                "My support style is calm and process-focused. "
                "I'd welcome a conversation about how that mix of support discipline "
                "could help Arize's IT team.",
            ],
            "closing": (
                "I'd like to discuss how my support background could help your team. "
                "Thank you for your time and consideration."
            ),
        }
        notes = integrity_guards.run_cover_letter_guards(
            content, job_title="IT Support Specialist", company="Arize"
        )
        last_body = content["body_paragraphs"][-1]
        # The closing-intent sentence is gone from the body paragraph.
        self.assertNotIn("welcome a conversation", last_body)
        # The substantive sentence before it survives.
        self.assertIn("My support style is calm", last_body)
        # The actual closing field is untouched.
        self.assertIn("Thank you for your time", content["closing"])
        self.assertTrue(any("duplicate closing-intent" in n for n in notes))

    def test_rewrites_from_company_side_of_the_work(self):
        # Arize leak: body paragraph had "What I bring from Arize's side of the
        # work is discipline..." which reads as awkward LLM mirror-speak.
        content = {
            "opening": "Hello.",
            "body_paragraphs": [
                "I worked at BTB on tickets.",
                "What I bring from Arize's side of the work is discipline around clear handoffs.",
            ],
            "closing": "Thanks.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content, job_title="IT Support Specialist", company="Arize AI"
        )
        self.assertIn(
            "What I bring to Arize is discipline around clear handoffs.",
            content["body_paragraphs"][1],
        )
        self.assertNotIn("side of the work", content["body_paragraphs"][1])
        self.assertTrue(any("side of the work" in n for n in notes))

    def test_rewrites_from_your_side_of_the_work(self):
        # Generic "your" variant — should drop to just "What I bring is..."
        content = {
            "opening": "What I bring from your side of the work is steady documentation.",
            "body_paragraphs": [],
            "closing": "Thanks.",
        }
        integrity_guards.run_cover_letter_guards(
            content, job_title="IT Support", company="Acme"
        )
        self.assertEqual(content["opening"], "What I bring is steady documentation.")

    def test_preserves_legitimate_side_phrasing(self):
        # Legitimate "side of the system" / "customer side" must NOT match.
        content = {
            "opening": "I worked on the customer side of the system.",
            "body_paragraphs": [],
            "closing": "Thanks.",
        }
        before = content["opening"]
        integrity_guards.run_cover_letter_guards(
            content, job_title="IT Support", company="Acme"
        )
        self.assertEqual(content["opening"], before)

    def test_no_strip_when_only_one_closing(self):
        # Single closing sentence in the closing field — body has no closing
        # intent — must not be touched.
        content = {
            "opening": "Hello.",
            "body_paragraphs": [
                "I bring three years of help desk experience.",
            ],
            "closing": "I'd welcome a conversation about this role. Thank you.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content, job_title="IT Support Specialist", company="Acme"
        )
        self.assertEqual(content["body_paragraphs"][-1], "I bring three years of help desk experience.")
        self.assertFalse(any("duplicate closing-intent" in n for n in notes))

    def test_replaces_casual_closing(self):
        content = {
            "opening": "Hello.",
            "body_paragraphs": ["Body."],
            "closing": "If useful, I can walk through the hardware work I have done.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content,
            job_title="Service Desk Technician",
            company="Digitech",
        )
        self.assertTrue(notes)
        self.assertIn("Digitech", content["closing"])
        self.assertNotIn("If useful", content["closing"])

    def test_strips_defensive_disclaimer_clause(self):
        content = {
            "opening": "Hello.",
            "body_paragraphs": [
                "I configured Local Group Policy on Windows endpoints, which is Windows policy configuration, not Active Directory administration."
            ],
            "closing": "Thanks.",
        }
        notes = integrity_guards.run_cover_letter_guards(
            content, job_title="Service Desk Technician", company="Logicalis"
        )
        self.assertIn(
            "I configured Local Group Policy on Windows endpoints.",
            content["body_paragraphs"][0],
        )
        self.assertNotIn("not Active Directory", content["body_paragraphs"][0])
        self.assertTrue(any("defensive disclaimer" in n for n in notes))


class TestVagueFillerBullets(unittest.TestCase):
    def test_drops_vague_filler_bullet(self):
        content = {
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Managed site technical systems.",
                        "Managed Salesforce queue for 30 daily tickets.",
                    ],
                }
            ]
        }
        notes = integrity_guards.strip_vague_filler_bullets(content)
        bullets = content["experience"][0]["bullets"]
        self.assertEqual(len(bullets), 1)
        self.assertIn("Salesforce", bullets[0])
        self.assertTrue(any("vague-filler" in n for n in notes))


class TestSummaryMinimumFlag(unittest.TestCase):
    def test_emits_note_without_rewrite(self):
        content = {"summary": "Service Desk Technician (Tier 1) with end-user support."}
        before = content["summary"]
        notes = integrity_guards.flag_summary_below_minimum(content)
        self.assertEqual(content["summary"], before)
        self.assertTrue(any("summary below minimum" in n for n in notes))


class TestHelpdeskSkillOrdering(unittest.TestCase):
    def test_helpdesk_stack_leads_for_tier1_role(self):
        content = {
            "skills": {
                "technical": [
                    "TCP/IP",
                    "Microsoft 365",
                    "Outlook",
                    "Windows OS troubleshooting",
                    "Help desk support",
                    "Ticketing/ITSM",
                    "DNS",
                ],
                "soft": [],
            }
        }
        notes = integrity_guards.reorder_skills_function_first(
            content, job_title="Service Desk Technician Tier 1"
        )
        tech = content["skills"]["technical"]
        self.assertLess(tech.index("Microsoft 365"), tech.index("TCP/IP"))
        self.assertLess(tech.index("Outlook"), tech.index("DNS"))
        self.assertTrue(any("help-desk stack first" in n for n in notes))


class TestSkillCleanup(unittest.TestCase):
    def test_sop_writing_merges_to_sops_runbooks(self):
        cleaned, notes = integrity_guards.dedupe_skills_semantic(
            ["SOP writing", "SOPs/Runbooks", "Ticketing/ITSM"]
        )
        self.assertEqual(cleaned.count("SOPs/Runbooks"), 1)
        self.assertTrue(notes)

    def test_customer_communication_moves_to_soft(self):
        content = {
            "skills": {
                "technical": ["Ticketing/ITSM", "Customer communication", "Microsoft 365"],
                "soft": ["Documentation"],
            }
        }
        notes = integrity_guards.relocate_soft_skills_from_technical(content)
        tech = content["skills"]["technical"]
        soft = content["skills"]["soft"]
        self.assertNotIn("Customer communication", tech)
        self.assertTrue(any("customer communication" in s.lower() for s in soft))
        self.assertTrue(notes)


class TestBrokenProseRepair(unittest.TestCase):
    def test_repairs_i_also_across_fragment(self):
        broken = (
            "At BEAT THE BOMB, I managed help desk requests. "
            "I also across hardware, operating systems, and local network layers."
        )
        fixed, notes = integrity_guards.fix_broken_missing_verb_prose(broken)
        self.assertIn("resolved live incidents across", fixed)
        self.assertNotIn("I also across", fixed)
        self.assertTrue(notes)

    def test_cover_letter_guard_applies_repair(self):
        content = {
            "opening": "Hello.",
            "body_paragraphs": [
                "I also across hardware, operating systems, and local network layers, "
                "and I wrote SOPs and runbooks."
            ],
            "closing": "Thanks.",
        }
        notes = integrity_guards.run_cover_letter_guards(content)
        body = content["body_paragraphs"][0]
        self.assertIn("resolved live incidents across", body)
        self.assertTrue(any("missing-verb" in n for n in notes))


class TestSummaryJobTitleLeak(unittest.TestCase):
    def test_strips_pasted_jd_title_prefix(self):
        content = {
            "summary": (
                "Service Desk Technician - Digitech - Remote IT support professional "
                "with hands-on troubleshooting experience."
            )
        }
        notes = integrity_guards.strip_job_title_leak_from_summary(
            content, job_title="Service Desk Technician - Digitech - Remote"
        )
        summary = content["summary"]
        self.assertNotIn("Digitech - Remote IT support professional", summary)
        self.assertTrue(summary.lower().startswith("service desk technician candidate"))
        self.assertTrue(notes)


class TestThinPhotonBullet(unittest.TestCase):
    def test_expands_vague_photon_bullet_for_helpdesk(self):
        content = {
            "experience": [
                {
                    "company": "BEAT THE BOMB",
                    "bullets": [
                        "Administered Linux Photon servers, NUC kiosks, and production networked infrastructure in a venue operations setting."
                    ],
                }
            ]
        }
        notes = integrity_guards.expand_thin_photon_infrastructure_bullet(
            content, job_title="Service Desk Technician - Digitech - Remote"
        )
        bullet = content["experience"][0]["bullets"][0]
        self.assertIn("networked facility systems", bullet)
        self.assertNotIn("venue operations setting", bullet)
        self.assertTrue(notes)

    def test_leaves_rich_photon_bullet_untouched(self):
        original = (
            "Administered Linux Photon servers, NUC kiosks, and networked infrastructure "
            "including CCTV, RFID, DMX, Dante audio, and OBS."
        )
        content = {"experience": [{"company": "BEAT THE BOMB", "bullets": [original]}]}
        integrity_guards.expand_thin_photon_infrastructure_bullet(
            content, job_title="Service Desk Technician"
        )
        self.assertEqual(content["experience"][0]["bullets"][0], original)


class TestSkillDisplayCase(unittest.TestCase):
    def test_lowercase_skills_canonicalize_to_title_case(self):
        cleaned, notes = integrity_guards.dedupe_skills_semantic(
            [
                "help desk",
                "desktop support",
                "account and access support",
                "documentation",
                "Windows OS troubleshooting",
            ]
        )
        self.assertIn("Help Desk Support", cleaned)
        self.assertIn("Desktop Support", cleaned)
        self.assertIn("Account & Access Support", cleaned)
        self.assertIn("Documentation", cleaned)
        self.assertIn("Windows OS Troubleshooting", cleaned)
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
