"""Provider-agnostic JSON generation for resume/cover-letter writing tasks."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from job_pipeline.claude_client import (
    AnthropicKeyMissingError,
    anthropic_api_key,
    claude_generate_json_with_retry,
)
from job_pipeline.genai_client import generate_content_with_retry
from job_pipeline.genai_json import parse_json_object_from_model
from job_pipeline.genai_settings import (
    DEFAULT_GEMINI_MODEL,
    claude_model_for,
    gemini_model_for,
    google_api_key,
    openai_model_for,
    writing_provider,
)
from job_pipeline.openai_client import (
    OpenAIKeyMissingError,
    openai_api_key,
    openai_generate_json_with_retry,
)

logger = logging.getLogger(__name__)


class LLMWritingError(RuntimeError):
    """All configured writing providers failed."""


def writing_providers_available() -> bool:
    return bool(openai_api_key() or google_api_key() or anthropic_api_key())


def writing_providers_missing_error() -> str:
    return (
        "No writing LLM configured: set OPENAI_API_KEY (or CHATGPT_API_KEY), "
        "GEMINI_API_KEY (or GOOGLE_API_KEY), or ANTHROPIC_API_KEY."
    )


def _provider_order() -> list[str]:
    """Pick primary provider then ordered fallbacks based on available keys."""
    primary = writing_provider()
    # Normalize "anthropic" alias to "claude".
    if primary == "anthropic":
        primary = "claude"
    # Build fallback chain — best alternates first, gated by which keys exist.
    fallbacks: list[str] = []
    if primary != "claude" and anthropic_api_key():
        fallbacks.append("claude")
    if primary != "openai" and openai_api_key():
        fallbacks.append("openai")
    if primary != "gemini" and google_api_key():
        fallbacks.append("gemini")
    return [primary] + fallbacks


def _gemini_generate_json(
    *,
    role: str,
    system: str,
    user: str,
    label: str,
    model: Optional[str],
    fallback_model: Optional[str],
    max_output_tokens: int,
) -> Dict[str, Any]:
    key = google_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY not set in environment")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai package not installed") from exc

    primary = (model or gemini_model_for(role)).strip()
    fb = (fallback_model if fallback_model is not None else DEFAULT_GEMINI_MODEL).strip()
    repair_suffix = (
        "\n\nCRITICAL: Return ONLY one valid JSON object. "
        "No markdown fences, no commentary, no text before or after the object."
    )
    models_to_try: list[str] = []
    for m in (primary, fb):
        if m and m not in models_to_try:
            models_to_try.append(m)

    json_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        max_output_tokens=max_output_tokens,
    )
    client = genai.Client(api_key=key)
    last_err: Optional[Exception] = None

    for model_name in models_to_try:
        for suffix in ("", repair_suffix):
            body = user + suffix
            if (system or "").strip():
                body = f"{system.strip()}\n\n{body}"
            try:
                resp = generate_content_with_retry(
                    client,
                    model=model_name,
                    contents=[types.Content(role="user", parts=[types.Part.from_text(text=body)])],
                    config=json_config,
                    fallback_model=None,
                    label=label or role,
                )
                text = getattr(resp, "text", None) or ""
                return parse_json_object_from_model(text)
            except ValueError as exc:
                last_err = exc
                logger.warning(
                    "%s Gemini JSON parse failed model=%s (%s); trying next attempt",
                    label,
                    model_name,
                    exc,
                )
            except Exception as exc:
                last_err = exc
                logger.warning("%s Gemini call failed model=%s (%s)", label, model_name, exc)
                break

    raise ValueError(str(last_err or "no JSON object in model response"))


def generate_json(
    role: str,
    *,
    system: str,
    user: str,
    label: str = "",
    max_retries: Optional[int] = None,
    base_sleep: Optional[float] = None,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    gemini_fallback_model: Optional[str] = DEFAULT_GEMINI_MODEL,
    openai_temperature: Optional[float] = None,
    gemini_max_output_tokens: int = 8192,
    claude_model: Optional[str] = None,
    claude_temperature: Optional[float] = None,
    claude_max_tokens: Optional[int] = None,
    user_cacheable_prefix: Optional[str] = None,
    system_cacheable_prefix: Optional[str] = None,
    cache_ttl: str = "1h",
) -> Dict[str, Any]:
    """
    Generate and parse a JSON object using the configured writing provider,
    falling back to other available providers on failure.

    Caching parameters (`system_cacheable_prefix`, `user_cacheable_prefix`,
    `cache_ttl`) only take effect on the Claude provider — Anthropic is the
    only backend with first-class prompt caching. On Gemini/OpenAI fallback,
    both prefixes are concatenated into the user message so the prompt is
    still complete, but no caching discount applies.
    """
    tag = label or role
    errors: list[str] = []
    # Non-Claude providers don't support per-block prompt caching, so we
    # concatenate BOTH cacheable prefixes into the user message for them.
    # Claude path receives the prefixes separately for actual cache control.
    user_for_non_claude = (
        (system_cacheable_prefix or "") + "\n\n"
        + (user_cacheable_prefix or "")
        + user
        if (user_cacheable_prefix or system_cacheable_prefix)
        else user
    )

    for provider in _provider_order():
        try:
            if provider == "claude":
                return claude_generate_json_with_retry(
                    model=claude_model or claude_model_for(role),
                    system=system,
                    user=user,
                    label=tag,
                    max_retries=max_retries,
                    base_sleep=base_sleep,
                    temperature=claude_temperature,
                    max_tokens=claude_max_tokens,
                    user_cacheable_prefix=user_cacheable_prefix,
                    system_cacheable_prefix=system_cacheable_prefix,
                    cache_ttl=cache_ttl,
                )
            if provider == "openai":
                return openai_generate_json_with_retry(
                    model=openai_model or openai_model_for(role),
                    system=system,
                    user=user_for_non_claude,
                    label=tag,
                    max_retries=max_retries,
                    base_sleep=base_sleep,
                    temperature=openai_temperature,
                )
            return _gemini_generate_json(
                role=role,
                system=system,
                user=user_for_non_claude,
                label=tag,
                model=gemini_model,
                fallback_model=gemini_fallback_model,
                max_output_tokens=gemini_max_output_tokens,
            )
        except (OpenAIKeyMissingError, AnthropicKeyMissingError) as exc:
            errors.append(str(exc))
            logger.warning("%s provider=%s key missing (%s); trying fallback", tag, provider, exc)
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            logger.warning("%s provider=%s failed (%s); trying fallback", tag, provider, exc)

    raise LLMWritingError("; ".join(errors) if errors else writing_providers_missing_error())
