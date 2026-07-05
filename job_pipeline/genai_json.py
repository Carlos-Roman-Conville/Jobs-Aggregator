"""Robust JSON extraction from Gemini model responses."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def _strip_markdown_fences(text: str) -> str:
    t = (text or "").strip()
    if "```" not in t:
        return t
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.IGNORECASE)
    return (m.group(1).strip() if m else t)


def _balanced_json_slice(text: str) -> Optional[str]:
    """Return the first balanced {...} object substring, respecting JSON strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _try_load(candidate: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_json_object_from_model(text: str) -> Dict[str, Any]:
    """
    Parse a JSON object from model output tolerant of markdown fences and leading prose.
    Raises ValueError when no valid object can be recovered.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model response")

    candidates: list[str] = []
    stripped = _strip_markdown_fences(raw)
    candidates.append(stripped)
    balanced = _balanced_json_slice(stripped)
    if balanced:
        candidates.append(balanced)
    m = re.search(r"\{[\s\S]*\}", stripped)
    if m:
        candidates.append(m.group(0))

    seen: set[str] = set()
    for cand in candidates:
        cand = cand.strip()
        if not cand or cand in seen:
            continue
        seen.add(cand)
        obj = _try_load(cand)
        if obj is not None:
            return obj

    raise ValueError("no JSON object in model response")


def preview_model_text(text: str, limit: int = 240) -> str:
    one_line = " ".join((text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"
