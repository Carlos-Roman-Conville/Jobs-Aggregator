"""Normalize the `company_name` field for human-facing output.

Some ingest sources (Indeed redirect URLs, partial scrapes) populate
`company_name` with the apply-URL host instead of a real company name —
e.g. `enterprisesolutioninc.com` instead of `Enterprise Solution Inc.`.
That bleeds straight into the cover letter address line ("Hiring Team,
enterprisesolutioninc.com") and looks unprofessional.

This module heuristically reconstructs a human-readable name from a
domain-shaped string. It's intentionally simple — there's no NLP, no
external lookup. It handles:

  - Pure domains (`acme.com` → `Acme`)
  - Subdomain prefixes (`solera.wd5.myworkdayjobs.com` → `Solera`)
  - Combined-word + suffix (`enterprisesolutioninc.com` → `Enterprise
    Solution Inc.`)
  - CamelCase domains (`TylerTech.com` → `Tyler Tech`)

When the input doesn't look like a domain (already has whitespace, or
no dot), it's returned unchanged.
"""
from __future__ import annotations

import re
from typing import List

# Common business suffixes to detect at the END of a compressed company
# string. Ordered longest-first so we don't half-strip "incorporated"
# down to "in" before checking "inc". Each entry maps the stripped
# match to the human-readable form to append.
_BUSINESS_SUFFIXES: List[tuple] = [
    ("incorporated", "Incorporated"),
    ("technologies", "Technologies"),
    ("solutions", "Solutions"),
    ("systems", "Systems"),
    ("partners", "Partners"),
    ("global", "Global"),
    ("group", "Group"),
    ("holdings", "Holdings"),
    ("ventures", "Ventures"),
    ("labs", "Labs"),
    ("works", "Works"),
    ("digital", "Digital"),
    ("media", "Media"),
    ("studios", "Studios"),
    ("corp", "Corp."),
    ("llc", "LLC"),
    ("ltd", "Ltd."),
    ("inc", "Inc."),
    ("ag", "AG"),
    ("gmbh", "GmbH"),
    ("sa", "S.A."),
    ("co", "Co."),
]

# Common content words to use as split-candidates inside a compressed
# domain string. Same-length-or-longer ordering so we don't split
# "enterprise" into "enter" + "prise" before trying the longer form.
_COMMON_WORDS: List[str] = sorted(
    [
        "enterprise", "solution", "service", "global", "national",
        "international", "advanced", "innovation", "innovative",
        "professional", "consulting", "industries", "industry",
        "automation", "intelligence", "platform", "network",
        "operations", "engineering", "technology", "software",
        "security", "research", "analytics", "wireless", "mobile",
        "online", "cloud", "labs", "tech",
        "data", "info", "soft", "ware", "sys", "med", "fin",
        "edu", "gov", "bio", "med", "auto", "eco", "agri",
        "construction", "logistics", "healthcare", "financial",
        "retail", "media", "digital", "creative", "studios",
        "design", "build", "build", "ware", "vision", "first",
        "energy", "power", "water", "earth", "metal", "steel",
    ],
    key=len,
    reverse=True,
)

# TLDs we strip. Anything not on this list stays as-is.
_KNOWN_TLDS = (
    "com", "net", "org", "io", "co", "us", "ai", "app",
    "tech", "biz", "info", "us", "uk", "de", "fr", "ca",
)


def _split_camelcase(text: str) -> str:
    """`TylerTech` -> `Tyler Tech`. Lower-followed-by-upper boundary."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)


def _split_compressed(text: str) -> str:
    """`enterprisesolutioninc` -> `enterprise solution inc`.

    Greedy left-to-right: pick the longest known word that prefixes the
    remaining string; if no known word matches, keep the rest intact.
    Detects business suffixes at the END as a separate step.
    """
    text = text.lower()
    if not text:
        return text

    # Peel off a business suffix first so it doesn't confuse the splitter.
    trailing_suffix = ""
    for suffix_lower, _ in _BUSINESS_SUFFIXES:
        if text.endswith(suffix_lower) and len(text) > len(suffix_lower):
            trailing_suffix = suffix_lower
            text = text[: -len(suffix_lower)]
            break

    parts: List[str] = []
    remaining = text
    while remaining:
        matched = False
        for word in _COMMON_WORDS:
            if remaining.startswith(word):
                parts.append(word)
                remaining = remaining[len(word):]
                matched = True
                break
        if not matched:
            # No known word at this prefix — emit the rest as one chunk.
            parts.append(remaining)
            remaining = ""

    if trailing_suffix:
        parts.append(trailing_suffix)

    return " ".join(parts)


def normalize_company_name(name: str) -> str:
    """Return a human-readable company name.

    Pure-string heuristic — does NOT hit the network, doesn't load a
    dictionary file. Pass through unchanged when the input already
    looks human-written.

    Examples:
      'enterprisesolutioninc.com' -> 'Enterprise Solution Inc.'
      'acmecorp.net'              -> 'Acme Corp.'
      'tylertech.com'             -> 'Tyler Tech'
      'TylerTech.com'             -> 'Tyler Tech'
      'Acme Inc'                  -> 'Acme Inc'  (already human)
      ''                          -> ''
    """
    raw = (name or "").strip()
    if not raw:
        return raw

    # If it already contains a space, it's a human-written name. Leave
    # the resume/cover letter writer to use it verbatim.
    if " " in raw:
        return raw

    # No dot AND no internal CamelCase — already a single-word name
    # (e.g. "Anthropic", "Stripe"). Just ensure it starts capitalized.
    if "." not in raw and not re.search(r"[a-z][A-Z]", raw):
        return raw[:1].upper() + raw[1:] if raw else raw

    # Strip a known TLD if present (and any trailing dot/slash chars).
    cleaned = raw.lower().strip("/")
    if "." in cleaned:
        segments = cleaned.split(".")
        # Drop trailing TLD segments only if recognized — otherwise the
        # dot might be part of a real name (rare for domains, but be safe).
        while len(segments) > 1 and segments[-1] in _KNOWN_TLDS:
            segments.pop()
        # If the leftmost segment is "www" or "jobs"/"careers", drop it.
        while len(segments) > 1 and segments[0] in ("www", "jobs", "careers", "apply", "app"):
            segments.pop(0)
        # Use the leftmost remaining segment as the company stem (handles
        # `solera.wd5.myworkdayjobs.com` -> `solera`).
        cleaned = segments[0]

    # If the input had CamelCase, expand it first. Otherwise try the
    # word-split heuristic on the compressed form.
    if re.search(r"[a-z][A-Z]", raw):
        # Use the original casing for the camelcase split (so we don't
        # over-split lowercased domains by accident).
        expanded = _split_camelcase(raw.split(".")[0])
    else:
        expanded = _split_compressed(cleaned)

    # Title-case each token; restore canonical business-suffix casing
    # (LLC, Inc., Ltd., etc.) when we recognize the last token.
    tokens = expanded.split()
    if not tokens:
        return raw

    canonical: List[str] = []
    for i, tok in enumerate(tokens):
        is_last = (i == len(tokens) - 1)
        lower = tok.lower()
        suffix_match = next(
            (rendered for k, rendered in _BUSINESS_SUFFIXES if k == lower),
            None,
        )
        if is_last and suffix_match:
            canonical.append(suffix_match)
        else:
            canonical.append(tok[:1].upper() + tok[1:].lower() if tok else tok)

    return " ".join(canonical)
