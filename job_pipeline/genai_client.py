"""Shared Gemini generate_content helper with capacity-aware retries."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from job_pipeline.genai_settings import DEFAULT_GEMINI_MODEL

logger = logging.getLogger(__name__)


def _max_retries() -> int:
    raw = (
        os.getenv("GEMINI_MAX_RETRIES")
        or os.getenv("GEMINI_SUMMARY_MAX_RETRIES")
        or "4"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def _retry_base_sec() -> float:
    raw = (
        os.getenv("GEMINI_RETRY_BASE_SEC")
        or os.getenv("GEMINI_SUMMARY_RETRY_BASE_SEC")
        or "5"
    ).strip()
    try:
        return max(0.5, float(raw))
    except ValueError:
        return 5.0


def is_retryable_capacity_error(exc: BaseException) -> bool:
    sc = getattr(exc, "status_code", None)
    if sc in (429, 500, 502, 503, 504):
        return True
    blob = str(exc).upper()
    if any(
        token in blob
        for token in ("503", "429", "500", "502", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
    ):
        return True
    es = str(exc).lower()
    return any(token in es for token in ("rate limit", "temporar", "overloaded", "high demand"))


def generate_content_with_retry(
    client: Any,
    *,
    model: str,
    contents: Any,
    config: Any = None,
    fallback_model: Optional[str] = DEFAULT_GEMINI_MODEL,
    max_retries: Optional[int] = None,
    base_sleep: Optional[float] = None,
    label: str = "gemini",
) -> Any:
    """
    Call client.models.generate_content with exponential backoff on transient errors.

    When the primary model is exhausted and fallback_model differs, retries on the
    fallback model as well. Re-raises the last exception if every attempt fails.
    """
    retries = max_retries if max_retries is not None else _max_retries()
    sleep_base = base_sleep if base_sleep is not None else _retry_base_sec()
    fb = (fallback_model or "").strip()
    models_to_try = [model]
    if fb and fb != model:
        models_to_try.append(fb)

    last_exc: Optional[BaseException] = None

    for model_idx, model_name in enumerate(models_to_try):
        for attempt in range(retries):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                if not is_retryable_capacity_error(exc):
                    raise

                is_last_attempt = attempt >= retries - 1
                is_last_model = model_idx >= len(models_to_try) - 1
                if is_last_attempt and is_last_model:
                    raise

                if is_last_attempt:
                    next_model = models_to_try[model_idx + 1] if model_idx + 1 < len(models_to_try) else ""
                    logger.warning(
                        "%s model=%s exhausted %s retries (%s); trying fallback=%s",
                        label,
                        model_name,
                        retries,
                        exc,
                        next_model or "none",
                    )
                    break

                delay = min(120.0, sleep_base * (1.6**attempt))
                logger.warning(
                    "%s model=%s attempt %s/%s failed (%s); retry in %.1fs",
                    label,
                    model_name,
                    attempt + 1,
                    retries,
                    exc,
                    delay,
                )
                time.sleep(delay)

    if last_exc:
        raise last_exc
    raise RuntimeError(f"{label}: generate_content failed with no response")
