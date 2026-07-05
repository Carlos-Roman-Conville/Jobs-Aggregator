"""Gated autonomous browser-fill helper powered by browser-use."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict

from application_assets import load_application_assets


def _runs_file() -> Path:
    root = Path(
        os.getenv("JOB_PIPELINE_BROWSER_USE_STATE_DIR")
        or Path.home() / ".cache" / "job_pipeline_browser"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root / "browser_use_runs.json"


def runs_today() -> int:
    p = _runs_file()
    if not p.is_file():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return 0
    stored = data.get(date.today().isoformat())
    return int(stored) if isinstance(stored, int) else 0


def increment_runs() -> int:
    p = _runs_file()
    n = runs_today() + 1
    payload: Dict[str, Any] = {}
    if p.is_file():
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload[date.today().isoformat()] = n
    p.write_text(json.dumps(payload), encoding="utf-8")
    return n


def run_job_apply_agent(
    job_url: str,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Lightweight wrapper — heavy dependency optional.
    Honour daily cap via env BROWSER_USE_MAX_RUNS_PER_DAY (default 5, 0 = unlimited).
    """
    cap = int(os.getenv("BROWSER_USE_MAX_RUNS_PER_DAY") or "5")
    if cap > 0 and runs_today() >= cap and not dry_run:
        return {
            "ok": False,
            "error": f"daily_budget_exhausted::{cap}",
            "job_url": job_url,
            "runs_today": runs_today(),
        }

    if dry_run:
        snippet = ""
        try:
            assets = json.loads(load_application_assets())
            snippet = f"{len(json.dumps(assets))} chars of application_assets snapshot"
        except Exception as exc:
            snippet = f"assets_missing:{exc}"
        return {
            "ok": True,
            "dry_run": True,
            "job_url": job_url,
            "budget_cap": cap,
            "runs_today": runs_today(),
            "note": snippet,
            "warning": (
                "Live browser-use submits are disabled in dry-run. "
                "Install browser-use, set JOB_PIPELINE_BROWSER_USE_ALLOW_EXECUTE=1, "
                "and pass dry_run=False only after manual risk review."
            ),
        }

    allow = os.getenv("JOB_PIPELINE_BROWSER_USE_ALLOW_EXECUTE", "").lower()
    if allow not in ("1", "true", "yes"):
        return {"ok": False, "error": "execution_disabled_set_JOB_PIPELINE_BROWSER_USE_ALLOW_EXECUTE"}

    try:
        from browser_use import Agent as _BUAgent  # type: ignore
    except ImportError:
        return {"ok": False, "error": "browser_use_package_not_installed"}

    prompt = (
        os.getenv("BROWSER_USE_JOB_APPLY_PROMPT")
        or "Open the applicant job URL as a courteous human recruiter assistant. "
        "Summarize mandatory fields ONLY — do not guess secrets."
    )
    persona = prompt + "\nURL: " + job_url
    increment_runs()
    try:
        _ = _BUAgent  # noqa: F841
        return {
            "ok": False,
            "error": "browser_use_agent_ready_but_not_configured",
            "hint": "Configure browser-use per upstream docs (LLM + Playwright).",
            "job_url": job_url,
            "persona_preview": persona[:400],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
