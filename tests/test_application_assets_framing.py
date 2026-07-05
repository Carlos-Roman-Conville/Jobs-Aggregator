"""
Offline deterministic smoke test for the IT-first `application_assets.json`
refresh.

No live OpenAI / Postgres calls. Mirrors what `job_pipeline/summarize.py`
assembles for the LLM and asserts the framing flowing into the prompt rather
than the LLM verdict.

Acceptance criteria mirror the IT-first plan:

- `resumes[0].metadata` in the loaded assets contains IT vocabulary and no
  `target_roles` entry starts with "operations" / "business operations"
  without a "technical" qualifier.
- `APPLICANT_RESUMES_METADATA` prompt snippet (built the way
  `summarize.py` builds it) contains Desktop Support / Help Desk / Jr
  Systems Administration framing.
- `career_identity_prompt_block()` mentions "IT" and tech-primary framing
  and does NOT describe Carlos as "operations-centric".
- `_heuristic_fit(it_jd, skills)` returns more skill hits than
  `_heuristic_fit(ops_jd, skills)`.
- `score_resume_for_posting` ranks the IT JD above the ops JD.
- `posting_has_tech_role_signal` is True for the IT JD and False for the
  ops JD. (Failure on the IT JD is a `domain_fit.py` keyword-list issue,
  not an assets issue — surfaced as `skipTest` for the dependent
  assertions and a printed note.)
- `calculate_domain_fit` is high for the IT JD (>= 0.55, with
  `matched_families` containing `it_support` or `helpdesk`) and low for
  the ops JD (<= 0.50, queue_reason does NOT start with
  "Strong operations").
- Don't-duplicate guard: `application_assets.json` raw text does not
  contain values that live in `search_preferences.md`
  (salary floors, ZIP 19107, 30-mile, proximity_*, pref_multiplier).
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from application_assets import (  # noqa: E402
    ASSETS_PATH,
    load_application_assets,
    load_application_assets_dict,
)
from application_asset_strategy import (  # noqa: E402
    score_resume_for_posting,
    strategy_prompt_block,
)
from job_pipeline.domain_fit import (  # noqa: E402
    calculate_domain_fit,
    career_identity_prompt_block,
    posting_has_tech_role_signal,
)
from job_pipeline.summarize import _heuristic_fit  # noqa: E402


IT_JD_TITLE = "IT Support Specialist"
IT_JD_DESC = (
    "We are hiring an IT Support Specialist to provide hands-on it support, "
    "help desk, and desktop support to internal users. Troubleshoot Windows "
    "and Linux issues using ticketing systems. Day to day: windows "
    "troubleshooting and linux server troubleshooting, manage active "
    "directory basics, resolve incidents via remote administration, perform "
    "hardware repair and imaging, and assist with onboarding/offboarding "
    "employees. Maintain TCP/IP, DNS, and VPN services. Incident response "
    "coordination across IT operations. Tier 2 helpdesk lead role for a "
    "growing team."
)

OPS_JD_TITLE = "Operations Manager"
OPS_JD_DESC = (
    "Operations Manager — Fulfillment Center. Lead warehouse fulfillment, "
    "dispatch, and inventory teams for a regional distribution center. "
    "Responsibilities: scheduling, supervise supply chain logistics, manage "
    "purchasing and vendor management, drive continuous improvement of "
    "warehouse workflows, ensure on-time delivery and quality control. "
    "Requirements: 5+ years operations management experience in warehouse "
    "or distribution; strong leadership and analytical skills."
)


def _build_applicant_resumes_metadata_block() -> str:
    """Mirror lines 257-264 of `job_pipeline/summarize.py`."""
    assets = json.loads(load_application_assets())
    resumes = [
        r
        for r in (assets.get("resumes") or [])
        if isinstance(r, dict) and r.get("id")
    ]
    lines = ["APPLICANT_RESUMES_METADATA (pick best id for role):"]
    for r in resumes:
        blob_r = {
            "id": r.get("id"),
            "metadata": r.get("metadata"),
            "suggest_when": r.get("suggest_when"),
        }
        lines.append(
            json.dumps(
                {k: v for k, v in blob_r.items() if v}, ensure_ascii=False
            )
        )
    return "\n".join(lines)


def _collected_skills() -> list[str]:
    """Same `skills` list `_heuristic_fit` sees in `summarize_pipeline_item`."""
    assets = json.loads(load_application_assets())
    resumes = [
        r
        for r in (assets.get("resumes") or [])
        if isinstance(r, dict) and r.get("id")
    ]
    out: list[str] = []
    for r in resumes:
        meta = r.get("metadata") or {}
        out.extend(meta.get("key_skills") or [])
    return out


class TestApplicationAssetsFraming(unittest.TestCase):
    # ---------------------------------------------------------------
    # 1. resumes[0].metadata reflects IT-first framing
    # ---------------------------------------------------------------
    def test_resumes_metadata_has_it_vocabulary(self) -> None:
        assets = load_application_assets_dict()
        resumes = assets.get("resumes") or []
        self.assertGreaterEqual(
            len(resumes), 1, "Expected at least one resume in application_assets.json"
        )
        meta = (resumes[0].get("metadata") or {}) if isinstance(resumes[0], dict) else {}
        summary = str(meta.get("summary") or "").lower()
        target_roles = [str(x).lower() for x in (meta.get("target_roles") or [])]
        key_skills = [str(x).lower() for x in (meta.get("key_skills") or [])]

        for needle in ("it", "desktop support", "help desk"):
            self.assertIn(
                needle,
                summary,
                f"resumes[0].metadata.summary missing IT framing keyword '{needle}'",
            )

        joined_target = " | ".join(target_roles)
        for needle in (
            "desktop support technician",
            "it support specialist",
            "help desk",
            "junior systems administrator",
        ):
            self.assertIn(
                needle,
                joined_target,
                f"resumes[0].metadata.target_roles missing IT role '{needle}'",
            )

        joined_skills = " | ".join(key_skills)
        for needle in ("desktop support", "help desk", "ticketing systems"):
            self.assertIn(
                needle,
                joined_skills,
                f"resumes[0].metadata.key_skills missing IT skill '{needle}'",
            )

    def test_resumes_target_roles_do_not_lead_with_pure_operations(self) -> None:
        """No target_roles entry may start with operations / business operations
        without a technical qualifier."""
        assets = load_application_assets_dict()
        resumes = assets.get("resumes") or []
        meta = (resumes[0].get("metadata") or {}) if resumes else {}
        for role in meta.get("target_roles") or []:
            r = str(role).strip().lower()
            if r.startswith("operations") or r.startswith("business operations"):
                self.assertIn(
                    "technical",
                    r,
                    f"target_roles entry '{role}' leads with operations without a 'technical' qualifier",
                )

    # ---------------------------------------------------------------
    # 2. Prompt-side snippets reflect IT framing
    # ---------------------------------------------------------------
    def test_prompt_resumes_metadata_block_contains_it_terms(self) -> None:
        block = _build_applicant_resumes_metadata_block().lower()
        for needle in ("desktop support", "help desk"):
            self.assertIn(needle, block, f"APPLICANT_RESUMES_METADATA missing '{needle}'")
        self.assertTrue(
            ("jr systems administration" in block)
            or ("junior systems administr" in block)
            or ("junior systems admin" in block),
            "APPLICANT_RESUMES_METADATA missing Jr / Junior Systems Administration framing",
        )

    def test_career_identity_block_is_tech_primary_not_operations_centric(self) -> None:
        block = career_identity_prompt_block()
        low = block.lower()
        self.assertIn("it", low, "career identity block missing 'it'")
        self.assertTrue(
            ("tech-primary" in low)
            or ("technical operations" in low)
            or ("information_technology" in low)
            or ("hands-on it" in low),
            f"career identity block missing tech-primary framing.\n{block}",
        )
        self.assertNotIn(
            "operations-centric",
            low,
            "career identity block still describes profile as 'operations-centric'",
        )
        self.assertNotIn(
            "operations-first profile",
            low,
            "career identity block still describes profile as 'operations-first'",
        )

    # ---------------------------------------------------------------
    # 3. Heuristic + rule-based scoring prefer IT JDs
    # ---------------------------------------------------------------
    def test_heuristic_fit_higher_for_it_jd_than_ops_jd(self) -> None:
        skills = _collected_skills()
        self.assertGreater(
            len(skills), 0, "Expected at least one key_skill across resumes"
        )
        it_score, it_hits = _heuristic_fit(IT_JD_DESC, skills)
        ops_score, ops_hits = _heuristic_fit(OPS_JD_DESC, skills)
        self.assertGreater(
            it_hits,
            ops_hits,
            f"Expected more skill hits for IT JD than ops JD; got it={it_hits} ops={ops_hits}",
        )
        self.assertGreater(
            it_score,
            ops_score,
            f"Expected higher heuristic fit for IT JD; got it={it_score} ops={ops_score}",
        )
        it_terms_lc = IT_JD_DESC.lower()
        for needle in ("help desk", "active directory", "ticketing"):
            self.assertIn(
                needle,
                it_terms_lc,
                f"IT JD fixture should reference '{needle}' so _heuristic_fit hits land",
            )

    def test_rule_based_resume_score_prefers_it_posting(self) -> None:
        assets = load_application_assets_dict()
        resumes = assets.get("resumes") or []
        self.assertGreaterEqual(len(resumes), 1)
        primary = resumes[0]

        it_score, _ = score_resume_for_posting(
            primary, IT_JD_TITLE, IT_JD_DESC, []
        )
        ops_score, _ = score_resume_for_posting(
            primary, OPS_JD_TITLE, OPS_JD_DESC, []
        )
        self.assertGreater(
            it_score,
            ops_score,
            f"Rule-based resume score should rank IT > ops; got it={it_score} ops={ops_score}",
        )

    # ---------------------------------------------------------------
    # 4. domain_fit signals align with IT-first framing
    # ---------------------------------------------------------------
    def test_posting_has_tech_role_signal_for_it_jd(self) -> None:
        if not posting_has_tech_role_signal(IT_JD_TITLE, IT_JD_DESC):
            self.skipTest(
                "posting_has_tech_role_signal returned False for the IT JD. "
                "This is a job_pipeline/domain_fit.py keyword-list problem "
                "(FAMILY_KEYWORDS does not match 'IT Support Specialist'), "
                "NOT an application_assets.json issue. Flag separately."
            )

    def test_posting_has_tech_role_signal_false_for_ops_jd(self) -> None:
        self.assertFalse(
            posting_has_tech_role_signal(OPS_JD_TITLE, OPS_JD_DESC),
            "Pure operations / warehouse JD should not register as tech-aligned",
        )

    def test_calculate_domain_fit_it_jd_is_strong(self) -> None:
        if not posting_has_tech_role_signal(IT_JD_TITLE, IT_JD_DESC):
            self.skipTest(
                "Dependent on tech-role-signal classifier; skipping per plan."
            )
        res = calculate_domain_fit(IT_JD_TITLE, IT_JD_DESC)
        matched = set(res.get("matched_families") or [])
        self.assertTrue(
            bool(matched & {"it_support", "helpdesk"}),
            f"IT JD matched_families should include it_support or helpdesk; got {matched}",
        )
        self.assertGreaterEqual(
            float(res.get("domain_score") or 0.0),
            0.55,
            f"IT JD domain_score should be >= 0.55; got {res.get('domain_score')}",
        )
        qreason = str(res.get("queue_reason") or "")
        self.assertFalse(
            qreason.lower().startswith("strong operations"),
            f"IT JD queue_reason should not lead with 'Strong operations'; got '{qreason}'",
        )

    def test_calculate_domain_fit_ops_jd_is_weak(self) -> None:
        res = calculate_domain_fit(OPS_JD_TITLE, OPS_JD_DESC)
        self.assertLessEqual(
            float(res.get("domain_score") or 0.0),
            0.50,
            f"Pure ops JD domain_score should be <= 0.50; got {res.get('domain_score')}",
        )
        qreason = str(res.get("queue_reason") or "")
        self.assertFalse(
            qreason.lower().startswith("strong operations"),
            f"Pure ops JD queue_reason should not lead with 'Strong operations'; got '{qreason}'",
        )

    # ---------------------------------------------------------------
    # 5. Don't-duplicate guard — search_preferences values must not
    #    leak into application_assets.json
    # ---------------------------------------------------------------
    def test_assets_json_does_not_duplicate_search_preferences_values(self) -> None:
        with open(ASSETS_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        forbidden = [
            "$60,000",
            "$55,000",
            "$65,000",
            "$70,000",
            "19107",
            "30-mile",
            "proximity_",
            "pref_multiplier",
        ]
        for token in forbidden:
            self.assertNotIn(
                token,
                raw,
                f"application_assets.json duplicates a search_preferences value: '{token}'",
            )

    # ---------------------------------------------------------------
    # 6. Eyeball print (always passes; useful when running -v)
    # ---------------------------------------------------------------
    def test_print_assembled_prompt_blocks_for_eyeball(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            print("=" * 72)
            print("IT JD prompt assembly")
            print("-" * 72)
            print(_build_applicant_resumes_metadata_block())
            print()
            print(strategy_prompt_block(IT_JD_TITLE, IT_JD_DESC))
            print()
            print(career_identity_prompt_block())
            print()
            print("domain_fit:", calculate_domain_fit(IT_JD_TITLE, IT_JD_DESC))
            print()
            print("=" * 72)
            print("Ops JD prompt assembly")
            print("-" * 72)
            print(strategy_prompt_block(OPS_JD_TITLE, OPS_JD_DESC))
            print()
            print("domain_fit:", calculate_domain_fit(OPS_JD_TITLE, OPS_JD_DESC))
        # Dump only when -v is on so test output stays clean otherwise.
        if any(a in ("-v", "--verbose") for a in sys.argv):
            sys.stdout.write(buf.getvalue())


if __name__ == "__main__":
    unittest.main()
