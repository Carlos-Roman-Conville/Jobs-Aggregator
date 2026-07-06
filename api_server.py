"""Jobs Aggregator — FastAPI server.

Endpoints for ingest, summarize, queue browsing, and stats.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from job_pipeline import service as svc

app = FastAPI(title="Jobs Aggregator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _api_key_required() -> bool:
    return bool((os.getenv("N8N_API_KEY") or "").strip())


def require_api_key_dep(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = (os.getenv("N8N_API_KEY") or "").strip()
    if not expected:
        return
    if (x_api_key or "").strip() != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


class DecisionRequest(BaseModel):
    action: str
    notes: str = ""


class ManualAddRequest(BaseModel):
    company_name: str
    title: str
    apply_url: str
    description_text: str = ""
    location: str = ""
    salary_text: str = ""


@app.get("/health")
def health() -> Dict[str, Any]:
    schema = svc.ensure_schema()
    return {"ok": bool(schema.get("ok")), "schema": schema}


@app.post("/jobs/ingest")
def ingest(_: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return svc.svc_ingest()


@app.post("/jobs/summarize")
def summarize(
    limit: int = 15, _: None = Depends(require_api_key_dep)
) -> Dict[str, Any]:
    return svc.svc_summarize(limit=limit)


@app.post("/jobs/daily-run")
def daily_run(
    ingest: bool = True,
    summarize_limit: int = 25,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return svc.svc_daily_run(ingest=bool(ingest), summarize_limit=int(summarize_limit))


@app.get("/jobs/queue")
def queue(
    status: Optional[str] = None,
    limit: int = 50,
    min_list_rank: Optional[float] = None,
    order_by_rank: bool = True,
    with_card: bool = False,
    source: Optional[str] = None,
    category: Optional[str] = None,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return svc.svc_queue(
        status=status,
        limit=limit,
        min_list_rank=min_list_rank,
        order_by_rank=order_by_rank,
        with_card=with_card,
        source=source,
        category=category,
    )


@app.get("/jobs/digest")
def digest(limit: int = 7, _: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return svc.svc_digest_pending(limit=limit)


@app.get("/jobs/stats")
def stats(_: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return svc.svc_pipeline_stats()


@app.get("/jobs/items/{item_id}")
def get_item(item_id: int, _: None = Depends(require_api_key_dep)) -> Dict[str, Any]:
    return svc.svc_get_item(item_id)


@app.post("/jobs/items/{item_id}/decision")
def decide(
    item_id: int,
    payload: DecisionRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return svc.svc_decide(item_id, payload.action, payload.notes)


@app.post("/jobs/manual")
def manual_add(
    payload: ManualAddRequest,
    _: None = Depends(require_api_key_dep),
) -> Dict[str, Any]:
    return svc.svc_manual_add(
        company_name=payload.company_name,
        title=payload.title,
        apply_url=payload.apply_url,
        description_text=payload.description_text,
        location=payload.location,
        salary_text=payload.salary_text,
    )
