"""OpenAI chat-completions helper for structured JSON writing tasks."""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional

from openai import OpenAI

from job_pipeline.genai_client import is_retryable_capacity_error
from job_pipeline.genai_json import parse_json_object_from_model

logger = logging.getLogger(__name__)


class OpenAIKeyMissingError(RuntimeError):
    """Raised when no OpenAI API key is configured."""


def openai_api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or os.getenv("CHATGPT_API_KEY") or "").strip()


def openai_api_key_missing_error() -> str:
    return "OPENAI_API_KEY or CHATGPT_API_KEY not set in environment"


def _max_retries() -> int:
    raw = (
        os.getenv("OPENAI_WRITING_MAX_RETRIES")
        or os.getenv("OPENAI_SUMMARY_MAX_RETRIES")
        or os.getenv("GEMINI_MAX_RETRIES")
        or "4"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def _retry_base_sec() -> float:
    raw = (
        os.getenv("OPENAI_WRITING_RETRY_BASE_SEC")
        or os.getenv("OPENAI_SUMMARY_RETRY_BASE_SEC")
        or os.getenv("GEMINI_RETRY_BASE_SEC")
        or "5"
    ).strip()
    try:
        return max(0.5, float(raw))
    except ValueError:
        return 5.0


def is_retryable_openai_error(exc: BaseException) -> bool:
    if is_retryable_capacity_error(exc):
        return True
    name = type(exc).__name__.lower()
    if any(token in name for token in ("timeout", "connection", "connect")):
        return True
    es = str(exc).lower()
    return any(token in es for token in ("timeout", "connection error", "connect error"))


def is_temperature_parameter_error(exc: BaseException) -> bool:
    es = str(exc).lower()
    return "temperature" in es and (
        "unsupported" in es
        or "does not support" in es
        or "invalid_request" in es
        or "unsupported_value" in es
    )


def model_fixed_temperature_family(model: str) -> bool:
    """Models that only accept the API default temperature (typically 1)."""
    m = (model or "").lower()
    if re.search(r"\bo[134](?:-|$|/|\.)", m):
        return True
    if "gpt-5" in m or "reasoning" in m:
        return True
    return False


def resolve_openai_temperature(
    model: str,
    *,
    explicit: Optional[float] = None,
    env_names: tuple[str, ...] = ("OPENAI_WRITING_TEMPERATURE", "OPENAI_TEMPERATURE"),
) -> Optional[float]:
    """
    Return temperature to send, or None to omit the parameter (API default).

    Set env OPENAI_WRITING_TEMPERATURE=0.2 for gpt-4.x chat models.
    Set OPENAI_WRITING_TEMPERATURE=default to always omit.
    """
    raw = ""
    for name in env_names:
        raw = (os.getenv(name) or "").strip()
        if raw:
            break
    if raw.lower() in ("default", "none"):
        return None
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    if explicit is not None:
        if model_fixed_temperature_family(model):
            return None
        return explicit
    if model_fixed_temperature_family(model):
        return None
    # Legacy chat models used in this repo tolerate low temperature.
    if re.search(r"gpt-4(?:\.\d+)?(?:-mini)?", (model or "").lower()):
        return 0.2
    return None


def _completion_kwargs(
    *,
    model_name: str,
    system: str,
    user: str,
    temperature: Optional[float],
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "model": model_name,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system or "Return exactly one valid JSON object."},
            {"role": "user", "content": user},
        ],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


def openai_generate_json_with_retry(
    *,
    model: str,
    system: str,
    user: str,
    label: str = "openai",
    max_retries: Optional[int] = None,
    base_sleep: Optional[float] = None,
    temperature: Optional[float] = None,
    client: Optional[Any] = None,
) -> dict:
    """
    Call OpenAI chat completions with JSON mode and exponential backoff.

    Returns a parsed JSON object dict. Re-raises the last exception when all
    retryable attempts are exhausted, or immediately on non-retryable errors.
    """
    key = openai_api_key()
    if not key:
        raise OpenAIKeyMissingError(openai_api_key_missing_error())

    retries = max_retries if max_retries is not None else _max_retries()
    sleep_base = base_sleep if base_sleep is not None else _retry_base_sec()
    oai = client or OpenAI(api_key=key)
    model_name = (model or "").strip()
    temp = resolve_openai_temperature(model_name, explicit=temperature)
    temp_attempts: list[Optional[float]] = []
    if temp is not None:
        temp_attempts.append(temp)
    temp_attempts.append(None)

    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        for use_temp in temp_attempts:
            try:
                resp = oai.chat.completions.create(
                    **_completion_kwargs(
                        model_name=model_name,
                        system=system,
                        user=user,
                        temperature=use_temp,
                    )
                )
                text = (resp.choices[0].message.content or "").strip()
                return parse_json_object_from_model(text)
            except Exception as exc:
                last_exc = exc
                if is_temperature_parameter_error(exc) and use_temp is not None:
                    logger.warning(
                        "%s model=%s rejected temperature=%s; retrying with API default",
                        label,
                        model_name,
                        use_temp,
                    )
                    continue
                if not is_retryable_openai_error(exc):
                    raise
                break
        else:
            continue
        if attempt >= retries - 1:
            raise last_exc  # type: ignore[misc]
        delay = min(120.0, sleep_base * (1.6**attempt))
        logger.warning(
            "%s model=%s attempt %s/%s failed (%s); retry in %.1fs",
            label,
            model_name,
            attempt + 1,
            retries,
            last_exc,
            delay,
        )
        time.sleep(delay)

    if last_exc:
        raise last_exc
    raise RuntimeError(f"{label}: openai chat completion failed with no response")
