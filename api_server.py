import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from job_pipeline import service as job_pipeline_service


class _StrayWebSocketFilter(logging.Filter):
    """Drop log spam from stray '/ws' WebSocket handshakes.

    This API serves no WebSocket route, so handshakes to '/ws' (e.g. from
    leftover ComfyUI browser tabs that auto-reconnect to this port) are
    correctly rejected with 403. Those clients retry forever and flood the
    console; this filter silences the noise without affecting real requests.
    """

    _NOISE = ("/ws?clientId=", "connection rejected (403 Forbidden)", "connection closed")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        return not any(token in message for token in self._NOISE)


for _logger_name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_logger_name).addFilter(_StrayWebSocketFilter())


app = FastAPI(title="Job Application Pipeline API", version="1.0.0")

# Permissive CORS so the dev/test flow (page-level fetch of /autofill/engine.js
# from ATS origins) works without restarting the extension. Production
# guidance is to install the browser extension itself, which bypasses CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/autofill/engine.js", response_class=PlainTextResponse)
def autofill_engine_source() -> str:
    """Return the bundled fill_engine.js source.

    Used for ad-hoc testing: from a page console you can run
    `fetch('http://127.0.0.1:8000/autofill/engine.js').then(r=>r.text()).then(eval)`
    to install window.JobPipelineAutofill without needing to reload the
    Chrome extension. Production path is the extension itself.
    """
    path = Path(__file__).resolve().parent / "browser_extension" / "fill_engine.js"
    return path.read_text(encoding="utf-8")


def _api_key_required() -> bool:
    return bool((os.getenv("N8N_API_KEY") or "").strip())


def require_api_key_dep(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = (os.getenv("N8N_API_KEY") or "").strip()
    if not expected:
        return
    if (x_api_key or "").strip() != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


class JobPipelineDecisionRequest(BaseModel):
    action: str
    notes: str = ""


class JobPipelinePackageRequest(BaseModel):
    resume_id: str = ""
    template_id: str = ""
    mode: str = "both"
    tailor_resume: bool = True
    attached_resume_path: str = ""


class JobPipelineTailorRequest(BaseModel):
    job_description: str
    job_title: str = ""
    company: str = ""
    location: str = ""
    mode: str = "both"
    tailor_resume: bool = True
    resume_id: str = ""
    template_id: str = ""
    attached_resume_path: str = ""
    render_cover_pdf: bool = True
    theme: str = "classic"


class JobPipelineOutcomeRequest(BaseModel):
    outcome: str
    notes: str = ""


class JobPipelineManualRequest(BaseModel):
    company_name: str
    title: str
    apply_url: str
    description_text: str = ""
    location: str = ""
    salary_text: str = ""


class CredentialSaveRequest(BaseModel):
    domain: str
    email: str
    password: str
    application_url: str = ""


@app.get("/health")
def health() -> Dict[str, Any]:
    schema = job_pipeline_service.ensure_schema()
    return {
        "ok": bool(schema.get("ok")),
        "api_key_required": _api_key_required(),
        "schema": schema,
    }


@app.get("/autofill/profile")
def autofill_profile() -> Dict[str, Any]:
    """Profile payload for the Firefox autofill extension (local use; no API key)."""
    return job_pipeline_service.svc_autofill_profile()


@app.get("/autofill/recent_resumes")
def autofill_recent_resumes(limit: int = 12) -> Dict[str, Any]:
    """List recently built tailored resume PDFs from generated_resumes/.

    Used by the autofill extension popup to tell the user which file to
    attach when a job application page has a file-upload input. Browsers
    block extensions from setting a file input from disk, so the next-best
    UX is making the right filename obvious.
    """
    return job_pipeline_service.svc_recent_resumes(limit=int(limit))


@app.get("/autofill/credentials")
def autofill_get_credential(domain: str = "") -> Dict[str, Any]:
    """Look up saved ATS account credentials for a domain.

    Used by the extension popup to surface "You already have an account on
    this site" when revisiting a Workday tenant (or any other ATS) after
    the initial account-create flow.
    """
    return job_pipeline_service.svc_get_credential(domain)


@app.post("/autofill/credentials")
def autofill_save_credential(payload: CredentialSaveRequest) -> Dict[str, Any]:
    """Save the email/password the extension just used on an account-create
    page, keyed by domain. Local-only file; no transmission anywhere.
    """
    return job_pipeline_service.svc_save_credential(
        domain=payload.domain,
        email=payload.email,
        password=payload.password,
        application_url=payload.application_url,
    )


@app.get("/autofill/tailored")
def autofill_tailored(url: str = "", filename: str = "") -> Dict[str, Any]:
    """Return per-job tailored content (skills line + summary + top bullets)
    for the package that best matches the given application URL.

    The extension uses this to fill JD-specific textareas like "What tools
    and systems are you most familiar with?" with the tailored skills line
    (which is JD-aware) instead of the static profile.
    """
    return job_pipeline_service.svc_tailored_for_url(url=url, filename=filename)


@app.post("/jobs/pipeline/ingest")
def jobs_pipeline_ingest(_: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return job_pipeline_service.svc_ingest()


@app.post("/jobs/pipeline/summarize")
def jobs_pipeline_summarize(limit: int = 15, _: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return job_pipeline_service.svc_summarize(limit=limit)


@app.post("/jobs/pipeline/rescore-domain")
def jobs_pipeline_rescore_domain(
    limit: int = Query(200, ge=1, le=2000),
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_rescore_domain_fit(limit=limit)


@app.post("/jobs/pipeline/daily-run")
def jobs_pipeline_daily_run(
    ingest: bool = True,
    summarize_limit: int = 25,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_daily_run(ingest=bool(ingest), summarize_limit=int(summarize_limit))


@app.get("/jobs/pipeline/queue")
def jobs_pipeline_queue(
    status: Optional[str] = None,
    limit: int = 50,
    min_list_rank: Optional[float] = None,
    order_by_rank: bool = True,
    with_card: bool = False,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_queue(
        status=status,
        limit=limit,
        min_list_rank=min_list_rank,
        order_by_rank=order_by_rank,
        with_card=with_card,
    )


@app.get("/jobs/pipeline/digest")
def jobs_pipeline_digest(limit: int = 7, _: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return job_pipeline_service.svc_digest_pending(limit=limit)


@app.get("/jobs/pipeline/analytics")
def jobs_pipeline_analytics(_: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return job_pipeline_service.svc_analytics()


@app.get("/jobs/pipeline/items/{item_id}")
def jobs_pipeline_item_get(item_id: int, _: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return job_pipeline_service.svc_get_item(item_id)


@app.post("/jobs/pipeline/items/{item_id}/decision")
def jobs_pipeline_decision(
    item_id: int,
    payload: JobPipelineDecisionRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_decide(item_id, payload.action, payload.notes)


@app.post("/jobs/pipeline/items/{item_id}/build-package")
def jobs_pipeline_build_package(
    item_id: int,
    payload: JobPipelinePackageRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    rid = payload.resume_id.strip() or None
    tid = payload.template_id.strip() or None
    attached = payload.attached_resume_path.strip() or None
    return job_pipeline_service.svc_build_package(
        item_id,
        resume_id=rid,
        template_id=tid,
        mode=payload.mode,
        tailor_resume=payload.tailor_resume,
        attached_resume_path=attached,
    )


@app.post("/jobs/pipeline/tailor")
def jobs_pipeline_tailor(
    payload: JobPipelineTailorRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    """
    Ad-hoc resume/cover-letter generation without a queue item_id.

    Localhost-only by convention: returns filesystem paths on the host running
    this API — not PDF bytes. No Postgres writes.
    """
    return job_pipeline_service.svc_tailor_application(
        job_description=payload.job_description,
        job_title=payload.job_title,
        company=payload.company,
        location=payload.location,
        mode=payload.mode,
        tailor_resume=payload.tailor_resume,
        resume_id=payload.resume_id.strip() or None,
        template_id=payload.template_id.strip() or None,
        attached_resume_path=payload.attached_resume_path.strip() or None,
        render_cover_pdf=payload.render_cover_pdf,
        theme=payload.theme,
    )


@app.post("/jobs/pipeline/items/{item_id}/outcome")
def jobs_pipeline_outcome(
    item_id: int,
    payload: JobPipelineOutcomeRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_record_outcome(item_id, payload.outcome, payload.notes)


@app.post("/jobs/pipeline/inbox-sync")
def jobs_pipeline_inbox_sync(
    dry_run: bool = True,
    max_messages: int = 120,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_imap_rejection_sync(
        dry_run=bool(dry_run),
        max_messages=int(max_messages),
    )


@app.post("/jobs/pipeline/items/{item_id}/browser-apply")
def jobs_pipeline_browser_apply(
    item_id: int,
    dry_run: bool = True,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_browser_apply_preview(item_id, dry_run=bool(dry_run))


@app.post("/jobs/pipeline/manual")
def jobs_pipeline_manual(
    payload: JobPipelineManualRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return job_pipeline_service.svc_manual_add(
        company_name=payload.company_name,
        title=payload.title,
        apply_url=payload.apply_url,
        description_text=payload.description_text,
        location=payload.location,
        salary_text=payload.salary_text,
    )
