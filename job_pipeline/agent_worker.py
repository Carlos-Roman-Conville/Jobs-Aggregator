"""
AgentWorker - per-Claude-Code-session wrapper around the SKIP LOCKED queue.

Each parallel Claude Code CLI session instantiates one AgentWorker with a
unique AGENT_ID and an ATS_FILTER. The worker encapsulates the boilerplate:
preflight, claim, build package, stage PDFs to a per-agent path (so three
agents don't overwrite each other's cover letters in Downloads), heartbeat,
release, and append to today's application log.

See MULTI_AGENT_APPLY_RUNBOOK.md for the paste-ready session startup prompt
and per-agent configuration table.

Typical usage from inside a Claude Code session:

    from job_pipeline.agent_worker import AgentWorker

    worker = AgentWorker(
        agent_id="auto-apply-greenhouse-1",
        ats_filter="%greenhouse%",
    )
    ok, errors = worker.preflight()
    assert ok, errors

    item = worker.claim_next()
    if item:
        result = worker.build_package(item["id"], mode="cover_letter_only")
        cover_pdf_for_upload = worker.stage_for_upload(
            result["cover_pdf"], kind="cover_letter"
        )
        # ... drive Chrome MCP, ping Carlos for human gates ...
        # heartbeat every 5 min while waiting:
        worker.heartbeat(item["id"])
        # On confirmation:
        worker.mark_submitted(item, confirmation_url="...")
        # On unrecoverable failure:
        # worker.mark_failed(item["id"], reason="404 listing")
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_pipeline.db import (
    DEFAULT_CLAIM_LEASE_MINUTES,
    claim_next_item,
    heartbeat_claim,
    pg_connect,
    release_claim,
)

# ---- Constants ------------------------------------------------------------

DEFAULT_DOWNLOADS = Path(
    os.environ.get("USERPROFILE", os.path.expanduser("~"))
) / "Downloads"

APPLICATION_LOG_DIR = (
    Path(__file__).resolve().parent.parent
    / "personal_docs"
    / "application_log"
)


# ---- Preflight result -----------------------------------------------------


class PreflightFailure(RuntimeError):
    """Raised when preflight checks fail and the worker should not proceed."""


# ---- Worker ---------------------------------------------------------------


class AgentWorker:
    """Per-session worker that wraps the SKIP LOCKED queue + package build +
    Downloads staging + application-log writes.

    Each AgentWorker instance owns:
      - a unique AGENT_ID (claim ownership key)
      - an ATS_FILTER (SQL ILIKE applied to job_postings.apply_url to scope
        which postings this session is responsible for)
      - a per-agent staging path in Downloads, so concurrent agents do not
        overwrite each other's cover letter / resume PDFs before Carlos has
        a chance to upload them. The canonical generic filename Carlos sees
        is suffixed with the AGENT_ID:
            Carlos_Roman-Conville_Cover_Letter_<AGENT_ID>.pdf
        Carlos's ATS uploader picker shows all three side-by-side and the
        agent's handoff message points at the exact one.
    """

    def __init__(
        self,
        agent_id: str,
        ats_filter: Optional[str] = None,
        *,
        downloads_dir: Optional[Path] = None,
        lease_minutes: int = DEFAULT_CLAIM_LEASE_MINUTES,
    ) -> None:
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        # ats_filter is OPTIONAL. The default (None or empty) means "pull
        # from the entire ranked pool" - SKIP LOCKED already prevents
        # double-grabs across agents, so partitioning by ATS family is only
        # useful as an OPTIMIZATION when you want a specific agent to
        # specialize in one ATS family. It is NOT required for correctness.
        normalized = (ats_filter or "").strip()
        if normalized and "%" not in normalized:
            raise ValueError(
                "ats_filter, when set, must be a SQL ILIKE pattern containing '%' "
                "(e.g. '%greenhouse%'). Pass None or empty string for no filter."
            )
        self.agent_id = agent_id.strip()
        self.ats_filter = normalized  # may be empty string meaning "no filter"
        self.downloads_dir = downloads_dir or DEFAULT_DOWNLOADS
        self.lease_minutes = int(lease_minutes)

    # ---- Filename helpers ------------------------------------------------

    @property
    def cover_letter_staging_name(self) -> str:
        return f"Carlos_Roman-Conville_Cover_Letter_{self.agent_id}.pdf"

    @property
    def resume_staging_name(self) -> str:
        return f"Carlos_Roman-Conville_Resume_{self.agent_id}.pdf"

    def cover_letter_staging_path(self) -> Path:
        return self.downloads_dir / self.cover_letter_staging_name

    def resume_staging_path(self) -> Path:
        return self.downloads_dir / self.resume_staging_name

    # ---- Preflight -------------------------------------------------------

    def preflight(self) -> Tuple[bool, List[str]]:
        """Verify the environment before any work. Returns (ok, errors)."""
        errors: List[str] = []

        # DB
        try:
            conn = pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM job_pipeline_items "
                    "WHERE claimed_by IS NULL LIMIT 1"
                )
            conn.close()
        except Exception as e:
            errors.append(
                f"Postgres unreachable or schema not migrated: {e}. "
                "Set POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD env "
                "vars and run init_job_pipeline_schema() once."
            )

        # Schema columns (the migration may not have run on this DB yet)
        try:
            conn = pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'job_pipeline_items' "
                    "AND column_name IN "
                    "('claimed_by', 'claimed_at', 'lease_expires_at')"
                )
                present = {r[0] for r in cur.fetchall()}
            conn.close()
            missing = {"claimed_by", "claimed_at", "lease_expires_at"} - present
            if missing:
                errors.append(
                    f"SKIP LOCKED columns missing: {sorted(missing)}. "
                    "Run init_job_pipeline_schema() to migrate."
                )
        except Exception as e:
            errors.append(f"Could not check schema columns: {e}")

        # Downloads dir
        try:
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Could not create Downloads dir {self.downloads_dir}: {e}")

        # Application-log dir
        try:
            APPLICATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(
                f"Could not create application-log dir {APPLICATION_LOG_DIR}: {e}"
            )

        # ats_filter sanity: empty/None means "no partition" which is the
        # correct default. A wildcard "%" is equivalent. Both are FINE -
        # SKIP LOCKED prevents double-grabs regardless. Only flag if the
        # value looks malformed (starts with garbage / no pattern).
        # (No warning needed for empty filter.)

        # Service module importable (validates the rest of the pipeline)
        try:
            from job_pipeline.service import svc_build_package, svc_decide  # noqa: F401
        except Exception as e:
            errors.append(
                f"job_pipeline.service import failed: {e}. "
                "The build_package path won't work until this is resolved."
            )

        return (not errors, errors)

    # ---- Queue operations ------------------------------------------------

    def claim_next(
        self,
        *,
        from_status: str = "pending_review",
    ) -> Optional[Dict[str, Any]]:
        """Claim the next eligible job awaiting human/agent action. Defaults
        to from_status='pending_review' since that's where the pipeline parks
        scored rows for review. Override to claim from a different gate (e.g.
        'ranked' if your ingestion+ranking pipeline leaves rows there).

        If ats_filter is set, narrows to that ATS slice; otherwise pulls the
        highest-fit row regardless of ATS family. SKIP LOCKED guarantees no
        double-grab either way.
        """
        if self.ats_filter:
            return claim_next_item(
                agent_id=self.agent_id,
                from_status=from_status,
                to_status="drafting",
                lease_minutes=self.lease_minutes,
                where_extra_sql=" AND p.apply_url ILIKE %s",
                where_extra_params=(self.ats_filter,),
            )
        return claim_next_item(
            agent_id=self.agent_id,
            from_status=from_status,
            to_status="drafting",
            lease_minutes=self.lease_minutes,
        )

    def heartbeat(self, item_id: int) -> bool:
        """Extend the lease. Call periodically (every ~5 min) during long
        browser-driven work so the reaper doesn't release the row.
        Returns True if our claim is still valid, False if it was reaped."""
        return heartbeat_claim(item_id, self.agent_id, lease_minutes=self.lease_minutes)

    def mark_submitted(
        self,
        item: Dict[str, Any],
        *,
        confirmation_url: str = "",
        notes: str = "",
    ) -> bool:
        """Release the claim with status=submitted and append a log entry.
        Returns True if release succeeded (ownership check passed)."""
        ok = release_claim(
            item["id"],
            self.agent_id,
            "submitted",
            notes=(notes or f"submitted via {self.agent_id}").strip(),
            require_ownership=True,
        )
        if ok:
            try:
                self._append_application_log(
                    item, status="SUBMITTED", confirmation_url=confirmation_url, notes=notes
                )
            except Exception as e:
                # Logging failure shouldn't undo the DB submission record.
                print(f"[{self.agent_id}] WARN: log append failed: {e}")
        return ok

    def mark_failed(self, item_id: int, reason: str) -> bool:
        """Release the claim with status=closed and a failure reason.
        Use for: 404 listings, login walls, blocked apply URLs, account-gated
        ATSes, anything irrecoverable. Returns True on success."""
        return release_claim(
            item_id,
            self.agent_id,
            "closed",
            notes=(reason or "irrecoverable")[:500],
            require_ownership=True,
        )

    # ---- Package build ---------------------------------------------------

    def build_package(
        self,
        item_id: int,
        *,
        mode: str = "cover_letter_only",
        tailor_resume: bool = False,
    ) -> Dict[str, Any]:
        """Run svc_decide(action=apply) then svc_build_package against the
        claimed item. Returns the build result dict (includes cover_pdf,
        resume_pdf paths and any tailor diagnostics).

        Claim ownership is NOT changed by this method - the lease stays with
        this agent through the status transitions ranked->drafting->approved
        ->package_ready. The reaper handles all three intermediate statuses.
        """
        from job_pipeline.service import svc_build_package, svc_decide

        decide_result = svc_decide(item_id, "apply", notes=f"claimed by {self.agent_id}")
        if not decide_result.get("ok"):
            return {
                "ok": False,
                "error": f"svc_decide failed: {decide_result.get('error', 'unknown')}",
                "stage": "svc_decide",
            }

        build_result = svc_build_package(
            item_id,
            resume_id=None,
            template_id=None,
            mode=mode,
            tailor_resume=bool(tailor_resume),
            attached_resume_path=None,
        )
        if not build_result.get("ok"):
            return {
                "ok": False,
                "error": f"svc_build_package failed: {build_result.get('error', 'unknown')}",
                "stage": "svc_build_package",
                **{k: v for k, v in build_result.items() if k not in {"ok", "error"}},
            }

        return build_result

    # ---- Staging ---------------------------------------------------------

    def stage_for_upload(
        self,
        source_path: Optional[str],
        *,
        kind: str = "cover_letter",
    ) -> Optional[Path]:
        """Copy a generated PDF to Downloads with the per-agent canonical
        filename, ready for Carlos to drop into the ATS file picker.

        kind must be 'cover_letter' or 'resume'. Returns the staged path or
        None if source_path was falsy/missing."""
        if not source_path:
            return None
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(
                f"Source PDF not found: {source_path} - did svc_build_package "
                "actually render a file? Check its diagnostics."
            )
        if kind == "cover_letter":
            dst = self.cover_letter_staging_path()
        elif kind == "resume":
            dst = self.resume_staging_path()
        else:
            raise ValueError(f"kind must be 'cover_letter' or 'resume', got {kind!r}")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    # ---- Application log -------------------------------------------------

    def _session_log_path(self) -> Path:
        d = date.today().isoformat()
        return APPLICATION_LOG_DIR / f"{d}_session.md"

    def _append_application_log(
        self,
        item: Dict[str, Any],
        *,
        status: str,
        confirmation_url: str = "",
        notes: str = "",
    ) -> Path:
        """Append a one-block entry to today's session log. Creates the file
        with a header if it doesn't exist yet."""
        log_path = self._session_log_path()
        company = item.get("company_name") or "(unknown)"
        title = item.get("title") or "(unknown)"
        location = item.get("location") or "(unknown)"
        apply_url = item.get("apply_url") or ""
        salary = item.get("salary_text") or ""
        posting_id = item.get("posting_id") or item.get("id")

        # Treat 0-byte files as "needs header" too — that's the state the
        # smoke test leaves behind after rolling its test log entry back.
        needs_header = (not log_path.exists()) or log_path.stat().st_size == 0
        if needs_header:
            APPLICATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"# Job Applications - {date.today().isoformat()} Session\n\n"
                "Driven via Claude Code + Chrome MCP (multi-agent).\n\n"
                "## Entries\n\n",
                encoding="utf-8",
            )

        block = [
            f"### {company} - {title}",
            "",
            f"- **Status:** {status}",
            f"- **Agent:** `{self.agent_id}`",
            f"- **Posting ID:** {posting_id}",
            f"- **Location:** {location}",
        ]
        if salary:
            block.append(f"- **Pay:** {salary}")
        if apply_url:
            block.append(f"- **Apply URL:** {apply_url}")
        if confirmation_url:
            block.append(f"- **Confirmation URL:** {confirmation_url}")
        if notes:
            block.append(f"- **Notes:** {notes}")
        block.append("")  # blank line

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(block) + "\n")
        return log_path

    # ---- Convenience info -------------------------------------------------

    def status_line(self) -> str:
        """One-line summary of this worker's identity + staging."""
        return (
            f"AgentWorker {self.agent_id} | ATS filter: {self.ats_filter} | "
            f"lease: {self.lease_minutes}min | "
            f"staging: {self.downloads_dir}"
        )
