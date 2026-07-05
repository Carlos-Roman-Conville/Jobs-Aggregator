"""Normalize posting URLs and text for dedupe and consistent matching."""
from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_TRACKING_QUERY_PREFIXES = ("utm_", "icid", "fbclid", "gclid", "mc_", "msclkid")
_TRACKING_QUERY_KEYS = frozenset(
    {"ref", "source", "campaign", "medium", "term", "content", "_ga", "_gl"}
)


def normalize_apply_url(url: str) -> str:
    """
    Canonical form for cross-source dedupe: scheme+host lowercase, strip www,
    strip common tracking query params, normalize path (no trailing slash except root).
    Returns "" if not a usable http(s) URL.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if not low.startswith(("http://", "https://")):
        return ""
    try:
        p = urlparse(raw)
        scheme = (p.scheme or "https").lower()
        netloc = (p.netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if not netloc:
            return ""
        path = (p.path or "").rstrip("/") or "/"
        qs = parse_qs(p.query, keep_blank_values=False)
        kept: Dict[str, list] = {}
        for k, vals in qs.items():
            kl = k.lower()
            if kl in _TRACKING_QUERY_KEYS:
                continue
            if any(kl.startswith(pref) for pref in _TRACKING_QUERY_PREFIXES):
                continue
            kept[k] = vals
        query = urlencode(kept, doseq=True)
        out = urlunparse((scheme, netloc, path, "", query, ""))
        return out
    except Exception:
        return low.split("?")[0].rstrip("/")


def normalize_whitespace(text: str, max_len: int | None = None) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if max_len is not None and len(t) > max_len:
        return t[:max_len]
    return t


def normalize_posting_fields(
    company_name: str,
    title: str,
    location: str,
    salary_text: str,
    description_text: str,
    *,
    max_desc: int = 12000,
) -> Dict[str, Any]:
    return {
        "company_name": normalize_whitespace(company_name, 500),
        "title": normalize_whitespace(title, 500) or "(no title)",
        "location": normalize_whitespace(location, 500),
        "salary_text": normalize_whitespace(salary_text, 500),
        "description_text": normalize_whitespace(description_text, max_desc),
    }
