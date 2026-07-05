"""
Resolve LLM API keys and model IDs from environment variables.

Usage split in this repo:
- **OpenAI** — job ingest summarization / scoring (`OPENAI_*` in `summarize.py`)
  and writing tasks when `LLM_WRITING_PROVIDER=openai` (default).
- **Gemini** — writing fallback and when `LLM_WRITING_PROVIDER=gemini`
  (`GEMINI_*` / `GOOGLE_API_KEY` here).

Per-task model env vars are checked first; then optional global fallbacks; then defaults.
"""
from __future__ import annotations

import os
from typing import Sequence

DEFAULT_GEMINI_MODEL = "models/gemini-2.5-flash"
DEFAULT_OPENAI_WRITING_MODEL = "gpt-4.1"
# Critique passes are a "find issues against a rubric" task — much easier than
# generation, so a smaller model handles it well at ~5x lower cost. The revise
# pass still uses the full writing model. Override with OPENAI_CRITIQUE_MODEL.
DEFAULT_OPENAI_CRITIQUE_MODEL = "gpt-4.1-mini"
# Claude defaults — Sonnet 4.6 is the latest Sonnet tier (best instruction-
# following + lowest hallucination rate among frontier non-Opus models).
# Haiku 4.5 for cheap critique passes. Switch to claude-opus-4-8 if you want
# the absolute strongest reasoning at higher cost.
DEFAULT_ANTHROPIC_WRITING_MODEL = "claude-sonnet-4-6"
DEFAULT_ANTHROPIC_CRITIQUE_MODEL = "claude-haiku-4-5-20251001"

_MISSING_KEY_HINT = "Set GEMINI_API_KEY or GOOGLE_API_KEY (same Google AI Studio / Gemini key)."


def google_api_key() -> str:
    """API key for `google.genai.Client` — Gemini or Google AI Studio."""
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def google_api_key_missing_error() -> str:
    """Unified message for structured error dicts / API responses."""
    return "GEMINI_API_KEY or GOOGLE_API_KEY not set in environment"


def missing_google_api_key_message() -> str:
    return _MISSING_KEY_HINT


def _first_env(names: Sequence[str]) -> str:
    for name in names:
        v = (os.getenv(name) or "").strip()
        if v:
            return v
    return ""


def gemini_model_for(role: str) -> str:
    """
    Pick the Gemini model ID for a pipeline role.

    Roles: bootstrap | tailor | gaps | career | cover_letter | package_check
    """
    r = (role or "").strip().lower().replace("-", "_")
    chains = {
        "bootstrap": (
            "GEMINI_RESUME_BOOTSTRAP_MODEL",
            "GEMINI_MODEL",
            "GEMINI_CAREER_MODEL",
            "GEMINI_JOB_SUMMARY_MODEL",
        ),
        "tailor": (
            "GEMINI_RESUME_TAILOR_MODEL",
            "GEMINI_MODEL",
            "GEMINI_CAREER_MODEL",
            "GEMINI_JOB_SUMMARY_MODEL",
        ),
        "gaps": (
            "GEMINI_RESUME_GAP_MODEL",
            "GEMINI_RESUME_TAILOR_MODEL",
            "GEMINI_MODEL",
            "GEMINI_CAREER_MODEL",
            "GEMINI_JOB_SUMMARY_MODEL",
        ),
        "career": (
            "GEMINI_CAREER_MODEL",
            "GEMINI_MODEL",
            "GEMINI_JOB_SUMMARY_MODEL",
        ),
        "cover_letter": (
            "GEMINI_JOB_APPLY_MODEL",
            "GEMINI_MODEL",
            "GEMINI_CAREER_MODEL",
            "GEMINI_JOB_SUMMARY_MODEL",
        ),
        "package_check": (
            "GEMINI_JOB_PACKAGE_CHECK_MODEL",
            "GEMINI_MODEL",
            "GEMINI_CAREER_MODEL",
            "GEMINI_JOB_SUMMARY_MODEL",
        ),
    }
    chain = chains.get(r) or (
        "GEMINI_MODEL",
        "GEMINI_CAREER_MODEL",
        "GEMINI_JOB_SUMMARY_MODEL",
    )
    return _first_env(chain) or DEFAULT_GEMINI_MODEL


def writing_provider() -> str:
    """Primary writing LLM backend: openai (default) | gemini | claude."""
    p = (os.getenv("LLM_WRITING_PROVIDER") or "openai").strip().lower()
    return p if p in ("openai", "gemini", "claude", "anthropic") else "openai"


def claude_model_for(role: str) -> str:
    """Pick the Claude model ID for a pipeline writing role.

    Roles: bootstrap | tailor | gaps | career | cover_letter | package_check | critique
    """
    r = (role or "").strip().lower().replace("-", "_")
    chains = {
        "bootstrap": (
            "ANTHROPIC_RESUME_BOOTSTRAP_MODEL",
            "ANTHROPIC_WRITING_MODEL",
        ),
        "tailor": (
            "ANTHROPIC_RESUME_TAILOR_MODEL",
            "ANTHROPIC_WRITING_MODEL",
        ),
        "gaps": (
            "ANTHROPIC_RESUME_GAP_MODEL",
            "ANTHROPIC_RESUME_TAILOR_MODEL",
            "ANTHROPIC_WRITING_MODEL",
        ),
        "career": (
            "ANTHROPIC_CAREER_MODEL",
            "ANTHROPIC_WRITING_MODEL",
        ),
        "cover_letter": (
            "ANTHROPIC_COVER_LETTER_MODEL",
            "ANTHROPIC_WRITING_MODEL",
        ),
        "package_check": (
            "ANTHROPIC_PACKAGE_CHECK_MODEL",
            "ANTHROPIC_WRITING_MODEL",
        ),
        "critique": (
            "ANTHROPIC_CRITIQUE_MODEL",
        ),
    }
    chain = chains.get(r) or ("ANTHROPIC_WRITING_MODEL",)
    resolved = _first_env(chain)
    if resolved:
        return resolved
    if r == "critique":
        return DEFAULT_ANTHROPIC_CRITIQUE_MODEL
    return DEFAULT_ANTHROPIC_WRITING_MODEL


def openai_model_for(role: str) -> str:
    """
    Pick the OpenAI model ID for a pipeline writing role.

    Roles: bootstrap | tailor | gaps | career | cover_letter | package_check
    """
    r = (role or "").strip().lower().replace("-", "_")
    chains = {
        "bootstrap": (
            "OPENAI_RESUME_BOOTSTRAP_MODEL",
            "OPENAI_WRITING_MODEL",
            "OPENAI_JOB_SUMMARY_MODEL",
        ),
        "tailor": (
            "OPENAI_RESUME_TAILOR_MODEL",
            "OPENAI_WRITING_MODEL",
            "OPENAI_JOB_SUMMARY_MODEL",
        ),
        "gaps": (
            "OPENAI_RESUME_GAP_MODEL",
            "OPENAI_RESUME_TAILOR_MODEL",
            "OPENAI_WRITING_MODEL",
            "OPENAI_JOB_SUMMARY_MODEL",
        ),
        "career": (
            "OPENAI_CAREER_MODEL",
            "OPENAI_WRITING_MODEL",
            "OPENAI_JOB_SUMMARY_MODEL",
        ),
        "cover_letter": (
            "OPENAI_COVER_LETTER_MODEL",
            "OPENAI_WRITING_MODEL",
            "OPENAI_JOB_SUMMARY_MODEL",
        ),
        "package_check": (
            "OPENAI_PACKAGE_CHECK_MODEL",
            "OPENAI_WRITING_MODEL",
            "OPENAI_JOB_SUMMARY_MODEL",
        ),
        # Critique passes use a smaller / cheaper model by default. Override
        # via OPENAI_CRITIQUE_MODEL or fall back to the writing model chain.
        "critique": (
            "OPENAI_CRITIQUE_MODEL",
        ),
    }
    chain = chains.get(r) or (
        "OPENAI_WRITING_MODEL",
        "OPENAI_JOB_SUMMARY_MODEL",
    )
    resolved = _first_env(chain)
    if resolved:
        return resolved
    if r == "critique":
        return DEFAULT_OPENAI_CRITIQUE_MODEL
    return DEFAULT_OPENAI_WRITING_MODEL
