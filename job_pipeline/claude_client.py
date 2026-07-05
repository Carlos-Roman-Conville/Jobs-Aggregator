"""Anthropic Claude messages helper for structured JSON writing tasks.

Mirrors openai_client.py so the rest of the codebase can route writing work
through Anthropic with the same retry / backoff / JSON-parsing semantics.

Why a defensive load_dotenv(override=True) at import time: Windows users
sometimes have ANTHROPIC_API_KEY set to an empty string at the OS-environment
level (from a prior shell session or a setx invocation). Python's default
load_dotenv() will NOT overwrite an existing OS env var even if it's empty,
which silently breaks key resolution. We force-load here so the key actually
makes it through.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Defensive env reload — Windows shells frequently inject an empty
# ANTHROPIC_API_KEY at the OS level that suppresses the .env value.
try:
    from dotenv import load_dotenv  # type: ignore

    # Only override-load if the key looks unset or empty, so we don't fight
    # an explicitly configured environment.
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        load_dotenv(override=True)
except ImportError:
    pass

from job_pipeline.genai_client import is_retryable_capacity_error
from job_pipeline.genai_json import parse_json_object_from_model


class AnthropicKeyMissingError(RuntimeError):
    """Raised when no Anthropic API key is configured."""


def anthropic_api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def anthropic_api_key_missing_error() -> str:
    return "ANTHROPIC_API_KEY not set in environment"


def _max_retries() -> int:
    raw = (
        os.getenv("ANTHROPIC_WRITING_MAX_RETRIES")
        or os.getenv("ANTHROPIC_MAX_RETRIES")
        or "4"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def _retry_base_sec() -> float:
    raw = (
        os.getenv("ANTHROPIC_WRITING_RETRY_BASE_SEC")
        or os.getenv("ANTHROPIC_RETRY_BASE_SEC")
        or "5"
    ).strip()
    try:
        return max(0.5, float(raw))
    except ValueError:
        return 5.0


def _max_tokens() -> int:
    """Output token budget. Tailored resumes are typically ~2.5-3.5K tokens."""
    raw = (os.getenv("ANTHROPIC_MAX_TOKENS") or "8192").strip()
    try:
        return max(512, int(raw))
    except ValueError:
        return 8192


def _resolve_temperature(explicit: Optional[float]) -> Optional[float]:
    """Resolve the temperature to send. Returns None to use API default."""
    raw = (os.getenv("ANTHROPIC_WRITING_TEMPERATURE") or "").strip()
    if raw.lower() in ("default", "none"):
        return None
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    if explicit is not None:
        return explicit
    # Tailoring + cover letter benefit from slight determinism. Match openai default.
    return 0.2


def is_retryable_anthropic_error(exc: BaseException) -> bool:
    if is_retryable_capacity_error(exc):
        return True
    name = type(exc).__name__.lower()
    if any(token in name for token in ("timeout", "connection", "connect", "overload", "rate")):
        return True
    es = str(exc).lower()
    return any(
        token in es
        for token in (
            "timeout",
            "connection error",
            "connect error",
            "overloaded",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    )


def _strip_code_fences(text: str) -> str:
    """Anthropic occasionally wraps JSON in markdown fences despite instructions."""
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", t, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else t


def claude_generate_json_with_retry(
    *,
    model: str,
    system: str,
    user: str,
    label: str = "claude",
    max_retries: Optional[int] = None,
    base_sleep: Optional[float] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    client: Optional[Any] = None,
    user_cacheable_prefix: Optional[str] = None,
    system_cacheable_prefix: Optional[str] = None,
    cache_ttl: str = "1h",
) -> dict:
    """Call Anthropic Messages API with strict-JSON instructions + retries.

    Returns a parsed JSON object dict. Re-raises the last exception when all
    retryable attempts are exhausted, or immediately on non-retryable errors.

    Anthropic's Messages API doesn't have a `response_format: json_object`
    equivalent like OpenAI; instead we (a) put a strong "return ONE JSON
    object only" instruction in the system prompt, (b) strip code fences if
    the model wraps the output, (c) run parse_json_object_from_model which
    is tolerant of trailing prose.

    Prompt caching is a PREFIX MATCH — any byte difference anywhere in the
    prefix invalidates everything after it. Render order is: `tools` →
    `system` → `messages`. So `system_cacheable_prefix` (which is the
    earlier and larger shared block — profile + truth-limits + style rules)
    gets first priority. `user_cacheable_prefix` is a second breakpoint on
    the user message for any extra shared content the caller wants to cache.

    `cache_ttl` defaults to "1h" (vs API default "5m"). Builds in this
    pipeline take 4-6 minutes and chain 5+ LLM calls; the 5-min TTL would
    expire mid-build and turn cache reads back into writes. 1-hour TTL
    doubles the write cost (2× vs 1.25×) but means a build only writes
    once and reads on every subsequent call. Net win is ~3-5× cost
    reduction for typical multi-call builds.

    Use `system_cacheable_prefix` for the LARGEST shared block (profile +
    truth-limits + style rules — should be 5-15K tokens). Use
    `user_cacheable_prefix` for additional shared content that has to live
    in the user message (e.g. per-build JD context shared across the
    resume + cover-letter calls). Verify cache hits via the `cache: read=N`
    log line — if read=0 across multiple calls in a build, a silent
    invalidator is at work (datetime.now in prefix, unsorted dict, etc.).
    """
    key = anthropic_api_key()
    if not key:
        raise AnthropicKeyMissingError(anthropic_api_key_missing_error())

    # Import here so the rest of the codebase doesn't depend on the SDK
    # being installed unless this code path is actually used.
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic SDK not installed. Run: pip install anthropic"
        ) from exc

    retries = max_retries if max_retries is not None else _max_retries()
    sleep_base = base_sleep if base_sleep is not None else _retry_base_sec()
    tok_budget = max_tokens if max_tokens is not None else _max_tokens()
    cli = client or Anthropic(api_key=key)
    model_name = (model or "claude-sonnet-4-5-20251022").strip()
    temp = _resolve_temperature(temperature)

    # Strengthen the system prompt with a hard JSON-only directive. The caller's
    # system prompt is preserved before this; Anthropic respects both.
    sys_prompt = (system or "").strip()
    json_guard = (
        "CRITICAL OUTPUT FORMAT: Return exactly ONE valid JSON object. "
        "Do NOT wrap the JSON in markdown code fences. "
        "Do NOT include any prose, commentary, or text before or after the JSON. "
        "Your entire response MUST be parseable as JSON.parse()."
    )
    final_system_text = f"{sys_prompt}\n\n{json_guard}" if sys_prompt else json_guard

    ttl_block: Dict[str, str] = {"type": "ephemeral"}
    if cache_ttl and cache_ttl != "5m":
        ttl_block = {"type": "ephemeral", "ttl": cache_ttl}

    # System prompt: when system_cacheable_prefix is provided, send system as
    # a LIST of content blocks with cache_control on the static prefix. This
    # is the largest-value cache opportunity since system renders before
    # messages — every byte of profile/truth-limits/style-rules served from
    # cache pays 10% of input price instead of 100%.
    system_prefix = (system_cacheable_prefix or "").strip()
    final_system: Any
    if system_prefix:
        final_system = [
            {
                "type": "text",
                "text": system_prefix,
                "cache_control": ttl_block,
            },
            # Per-call dynamic system text (small — role-specific guardrails
            # + the JSON output directive). NO cache_control: this block is
            # what varies between writer / critic / proofread roles.
            {"type": "text", "text": final_system_text},
        ]
    else:
        final_system = final_system_text

    # Build user message — either a plain string (no caching) or a list of
    # content blocks (with the prefix marked cacheable). The user prefix is
    # a SECONDARY cache opportunity — use it when there's shared content
    # that has to live in the user message (e.g. JD text shared between
    # the resume call and cover-letter call inside the same build).
    user_prefix = (user_cacheable_prefix or "").strip()
    if user_prefix:
        user_content: Any = [
            {
                "type": "text",
                "text": user_prefix,
                "cache_control": ttl_block,
            },
            {"type": "text", "text": (user or "").strip()},
        ]
    else:
        user_content = user

    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            create_kwargs: Dict[str, Any] = {
                "model": model_name,
                "max_tokens": tok_budget,
                "system": final_system,
                "messages": [{"role": "user", "content": user_content}],
            }
            if temp is not None:
                create_kwargs["temperature"] = temp
            resp = cli.messages.create(**create_kwargs)
            # Log cache hit/miss for EVERY claude call so a missing cache
            # read is obvious. Format: `[claude.<label>] in=X cache_r=Y
            # cache_w=Z out=W` — one line per call, scannable at a glance.
            usage = getattr(resp, "usage", None)
            if usage is not None:
                in_tokens = getattr(usage, "input_tokens", 0) or 0
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
                out_tokens = getattr(usage, "output_tokens", 0) or 0
                total_in = in_tokens + cache_read + cache_create
                hit_pct = (cache_read / total_in * 100) if total_in else 0
                logger.info(
                    "[claude.%s] model=%s in=%d cache_r=%d cache_w=%d out=%d hit=%.0f%%",
                    label, model_name, in_tokens, cache_read, cache_create, out_tokens, hit_pct,
                )
                if (system_prefix or user_prefix) and cache_read == 0 and cache_create == 0:
                    # Caching was requested but neither read nor wrote. Either
                    # the prefix was below the model's minimum, or a silent
                    # invalidator changed the prefix bytes since the last call.
                    logger.warning(
                        "[claude.%s] cache requested but neither read nor wrote — "
                        "prefix may be below %d-token minimum, or a silent invalidator "
                        "changed prefix bytes since last call (datetime, uuid, "
                        "non-deterministic dict, varying tool set, etc.)",
                        label, 2048,
                    )
            # Anthropic returns a list of content blocks; concatenate text blocks.
            text_parts: List[str] = []
            for block in (resp.content or []):
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_parts.append(getattr(block, "text", "") or "")
            text = _strip_code_fences("".join(text_parts).strip())
            return parse_json_object_from_model(text)
        except Exception as exc:
            last_exc = exc
            if not is_retryable_anthropic_error(exc):
                raise
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
    raise RuntimeError(f"{label}: claude messages call failed with no response")
