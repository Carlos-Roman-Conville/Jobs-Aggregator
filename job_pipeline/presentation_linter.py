"""
Deterministic presentation linter for tailored resume + cover-letter content.

WHY THIS EXISTS
---------------
Objective, rule-shaped defects (capitalization, banned phrases, semicolon-packed
bullets, generic summaries, informal cover-letter phrasing) were being left to the
LLM critique loop, which is non-deterministic — it catches a different subset every
run. That is why "obvious" things slipped through inconsistently and the package
score oscillated. This module moves every objective defect to a deterministic pass
that runs the SAME way every time and gets the LAST WORD before export.

CONTRACT
--------
* Rules live in ``style_rules.yaml`` (single source of truth). Edit the YAML, not
  this file, to change behavior.
* This linter NEVER overrides truthfulness gates (truth_classifier / evidence_db /
  named_requirements / light_exposure). It is cosmetic: it rewords/recases/flags,
  it never invents or inflates a claim.
* It must NEVER crash a build. Every public entry point is defensive; on any
  internal error it returns the content unchanged with a note.

PUBLIC API
----------
    load_rules(path=None) -> dict
    lint_resume(content, *, job_title="", jd_text="", rules=None) -> LintResult
    lint_cover_letter(content, *, company="", role="", jd_text="", rules=None) -> LintResult
    cross_document_consistency(resume, cover_letter, *, role="", company="", rules=None) -> list[Finding]
    presentation_penalty(findings, rules=None) -> float

A ``LintResult`` carries the (possibly autofixed) ``content``, the list of
``findings``, human-readable ``notes`` (same style as the optimizer's opt_notes),
and the computed ``penalty`` to subtract from the 0-100 rubric score.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Severities
AUTOFIX = "autofix"
WARN = "warn"
BLOCK = "block"

_RULES_FILENAME = "style_rules.yaml"
_rules_cache: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    rule_id: str
    severity: str
    location: str          # field path, e.g. "experience[0].bullets[2]"
    message: str
    original: str = ""
    suggestion: str = ""

    def as_note(self) -> str:
        tag = {AUTOFIX: "fixed", WARN: "warn", BLOCK: "BLOCK"}.get(self.severity, self.severity)
        return f"presentation[{tag}] {self.rule_id} @ {self.location}: {self.message}"


@dataclass
class LintResult:
    content: Dict[str, Any]
    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    penalty: float = 0.0

    @property
    def blocking(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == BLOCK]


# ---------------------------------------------------------------------------
# Rules loading (yaml source, embedded fallback so a build never crashes)
# ---------------------------------------------------------------------------
def load_rules(path: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, Any]:
    """Load style_rules.yaml. Cached. Falls back to a small embedded subset if
    pyyaml or the file is unavailable, so the linter degrades instead of failing."""
    global _rules_cache
    if _rules_cache is not None and not force_reload and path is None:
        return _rules_cache

    rules: Optional[Dict[str, Any]] = None
    candidate = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), _RULES_FILENAME)
    try:
        import yaml  # type: ignore

        with open(candidate, "r", encoding="utf-8") as fh:
            rules = yaml.safe_load(fh) or {}
    except ImportError:
        logger.warning("presentation_linter: pyyaml not installed; using embedded fallback rules")
    except FileNotFoundError:
        logger.warning("presentation_linter: %s not found; using embedded fallback rules", candidate)
    except Exception as exc:  # malformed yaml etc.
        logger.warning("presentation_linter: failed to load rules (%s); using embedded fallback", exc)

    if not rules:
        rules = _embedded_fallback_rules()

    if path is None:
        _rules_cache = rules
    return rules


def _embedded_fallback_rules() -> Dict[str, Any]:
    """Minimal but functional subset used only when the YAML can't be read."""
    return {
        "canonical_casing": {
            "m365": "Microsoft 365", "microsoft 365": "Microsoft 365", "o365": "Microsoft 365",
            "windows": "Windows", "windows os": "Windows OS", "powershell": "PowerShell",
            "macos": "macOS", "active directory": "Active Directory", "help desk": "Help Desk",
            "ticketing": "Ticketing/ITSM", "itsm": "Ticketing/ITSM", "salesforce": "Salesforce",
        },
        "acronyms_uppercase": ["IT", "PC", "OS", "DNS", "VPN", "SSO", "MFA", "ITSM", "SOP", "SOPs",
                                "TCP/IP", "API", "SQL", "PST", "AD"],
        "prose_proper_nouns": {"windows os": "Windows OS", "windows": "Windows",
                                "powershell": "PowerShell", "macos": "macOS",
                                "active directory": "Active Directory", "salesforce": "Salesforce"},
        "synonym_map": [
            {"canonical": "Ticketing/ITSM", "variants": ["ticketing", "itsm", "ticketing / itsm", "ticket system"]},
            {"canonical": "Microsoft 365", "variants": ["m365", "o365", "office 365"]},
        ],
        "banned_phrases": {
            "hype": ["revolutionized", "transformed", "world-class", "synergy", "single-handedly"],
            "vague_verbs": ["leveraged", "utilized", "spearheaded"],
            "hedges": ["help-desk-adjacent", "-adjacent", "i believe", "i think"],
            "informal": ["break/fix work", "a lot of", "stuff"],
            "cliches": ["team player", "detail-oriented", "proven track record", "passionate about"],
            "ai_tells": ["perfectly aligns", "confident in my ability", "delve"],
            "generic_openers": ["i am writing to apply", "to whom it may concern"],
            "groveling": ["i would be honored", "at your earliest convenience"],
        },
        "phrase_replacements": {
            "leveraged": "used", "utilized": "used", "spearheaded": "led",
            "help-desk-adjacent": "practical help desk and technical operations",
            "break/fix work": "break/fix support", "detail-oriented": "thorough",
        },
        "weak_bullet_starts": ["responsible for", "duties included", "worked on", "helped with",
                                "tasked with", "assisted with", "in charge of"],
        "bullets": {"max_chars": 240, "max_words": 34, "min_per_role": 3, "max_per_role": 6,
                     "forbid_semicolon": True, "forbid_first_person": True,
                     "require_action_verb_start": True, "max_slashes_per_line": 2},
        "summary": {"max_chars": 520, "max_sentences": 4, "must_open_with_target_title": True,
                     "title_lead_template": "{title} candidate",
                     "forbidden_openers": ["remote candidate", "experienced candidate",
                                            "motivated professional", "results-driven", "a candidate"]},
        "skills": {"technical_cap": 22, "soft_cap": 12, "case_style": "title",
                    "enforce_canonical_casing": True, "apply_synonym_map": True,
                    "drop_orphan_fragments": ["(user-level)", "(basic)", "()"],
                    "drop_tags": ["(study)", "(learning)"], "drop_tags_unless_jd_mentions": True,
                    "forbid_duplicates": True},
        "punctuation": {"forbid_double_space": True, "straight_quotes": True,
                         "forbid_space_before_punctuation": True, "date_range_dash": "–"},
        "cover_letter": {"min_words": 180, "max_words": 400, "max_body_paragraphs": 3,
                          "require_company_name": True, "require_role_name": True,
                          "forbid_generic_opener": True, "forbid_groveling": True},
        "cross_document": {"check_target_title_match": True, "check_years_experience_match": True,
                            "check_company_name_match": True},
        "parser": {"forbid_glyphs": ["�", "￾"], "forbid_db_date_format": True},
        "severity_defaults": {},
        "penalty": {"per_warn": 0.75, "per_block": 3.0, "max_total": 15.0},
    }


def _sev(rules: Dict[str, Any], rule_id: str, klass: str, default: str) -> str:
    sd = rules.get("severity_defaults") or {}
    if rule_id in sd:
        return sd[rule_id]
    if klass in sd:
        return sd[klass]
    return default


# ---------------------------------------------------------------------------
# Text-level helpers
# ---------------------------------------------------------------------------
_CURLY = {"‘": "'", "’": "'", "“": '"', "”": '"'}  # dashes handled by the dash-policy pass
_PRONOUN_STARTS = ("i ", "i'", "my ", "me ", "we ", "our ")


def _title_case_token(item: str, rules: Dict[str, Any]) -> str:
    """Casing for a single skill item: canonical map > acronym > word-wise title."""
    raw = item.strip()
    if not raw:
        return raw
    cc = {k.lower(): v for k, v in (rules.get("canonical_casing") or {}).items()}
    key = raw.lower()
    if key in cc:
        return cc[key]
    acronyms = {a.upper() for a in (rules.get("acronyms_uppercase") or [])}
    if raw.upper() in acronyms:
        return raw.upper()

    def fix_word(w: str) -> str:
        if not w:
            return w
        wl = w.lower()
        # only map a sub-word via the canonical map when the mapped value is a
        # simple single token — otherwise "ticketing" -> "Ticketing/ITSM" would
        # re-expand inside an already-canonical "Ticketing/ITSM" item.
        if wl in cc and "/" not in cc[wl] and " " not in cc[wl]:
            return cc[wl]
        if "/" in w:
            return "/".join(fix_word(p) for p in w.split("/"))
        if w.upper() in acronyms:
            return w.upper()
        # keep existing internal capitals (e.g. macOS, OneDrive) if already mixed
        if any(c.isupper() for c in w[1:]):
            return w
        return w[:1].upper() + w[1:].lower()

    return " ".join(fix_word(w) for w in raw.split())


def _match_case(matched: str, replacement: str) -> str:
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _word_boundary_pattern(phrase: str) -> "re.Pattern":
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)


def _apply_text_autofixes(text: str, rules: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Deterministic text rewrites: phrase replacements, quotes, spacing, dashes."""
    out = text or ""
    changed: List[str] = []

    # straight quotes (ATS-safe)
    if (rules.get("punctuation") or {}).get("straight_quotes"):
        for bad, good in _CURLY.items():
            if bad in out:
                out = out.replace(bad, good)
                changed.append("normalized smart punctuation")

    # phrase replacements (vague verbs / ai-tells with safe 1:1 mapping)
    for bad, good in (rules.get("phrase_replacements") or {}).items():
        pat = _word_boundary_pattern(bad)

        def _repl(m: "re.Match", g: str = good) -> str:
            return _match_case(m.group(0), g)

        new, n = pat.subn(_repl, out)
        if n:
            out = new
            changed.append(f"replaced '{bad}' -> '{good}' ({n}x)")

    # conservative proper-noun casing in prose (unambiguous tokens only; longest
    # match first so "windows os" -> "Windows OS" before "windows" -> "Windows").
    ppn = rules.get("prose_proper_nouns") or {}
    for key in sorted(ppn.keys(), key=len, reverse=True):
        proper = ppn[key]
        pat = re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)", re.IGNORECASE)
        new = pat.sub(lambda m, p=proper: p, out)
        if new != out:
            out = new
            changed.append(f"recased proper noun -> '{proper}'")

    punct = rules.get("punctuation") or {}
    if punct.get("forbid_space_before_punctuation"):
        new, n = re.subn(r"[ \t]+([,.;:!?])", r"\1", out)
        if n:
            out = new
            changed.append("removed space before punctuation")
    if punct.get("collapse_repeated_punctuation"):
        out = re.sub(r"([!?.]){2,}", r"\1", out)
    if punct.get("forbid_double_space"):
        new, n = re.subn(r"  +", " ", out)
        if n:
            out = new
            changed.append("collapsed double spaces")

    # date ranges like "Sept 2024 - Mar 2026" / "2024--2026" -> single hyphen
    dash = punct.get("date_range_dash") or "-"
    new, n = re.subn(
        r"(\b(?:[A-Z][a-z]{2,8}\.?\s)?\d{4})\s*(?:-{1,2}|—|–)\s*((?:[A-Z][a-z]{2,8}\.?\s)?\d{4}|[Pp]resent)\b",
        rf"\1 {dash} \2",
        out,
    )
    if n:
        out = new
        changed.append("normalized date range dash")

    # Dash policy: em dash and en dash are AI tells in resumes / cover letters.
    # The plain hyphen stays allowed (compound modifiers, number/date ranges,
    # phone numbers). This runs AFTER date-range normalization, so date dashes are
    # already hyphens and survive untouched.
    if punct.get("forbid_em_dash") or punct.get("forbid_en_dash"):
        before = out
        out = re.sub(r"(\d)\s*[—–]\s*(\d)", r"\1-\2", out)   # number ranges -> hyphen
        out = re.sub(r"\s+[—–]\s+", ", ", out)               # spaced connector -> comma
        out = re.sub(r"[—–]", ", ", out)                     # any stray em/en dash -> comma
        out = re.sub(r",\s*,", ", ", out)                    # collapse doubled commas
        out = re.sub(r"[ \t]{2,}", " ", out)                 # collapse doubled spaces
        if out != before:
            changed.append("removed em/en dash (AI tell)")

    if punct.get("forbid_trailing_whitespace"):
        out = "\n".join(line.rstrip() for line in out.split("\n"))

    return out, changed


def _scan_banned(text: str, rules: Dict[str, Any], location: str) -> List[Finding]:
    """Warn-level scan for remaining banned phrases (after autofix)."""
    findings: List[Finding] = []
    low = (text or "").lower()
    classes = {
        "hype": "hype", "vague_verbs": "vague_verb", "hedges": "hedge",
        "informal": "informal", "cliches": "cliche", "ai_tells": "ai_tell",
        "generic_openers": "generic_opener", "groveling": "groveling",
    }
    bp = rules.get("banned_phrases") or {}
    seen = set()
    for group, klass in classes.items():
        for phrase in bp.get(group) or []:
            p = str(phrase).strip().lower()
            if not p or p in seen:
                continue
            if p in low:
                seen.add(p)
                sev = _sev(rules, klass, klass, WARN)
                findings.append(Finding(
                    rule_id=klass, severity=sev, location=location,
                    message=f"{klass.replace('_', ' ')} phrase: '{phrase}'",
                    original=phrase,
                ))
    return findings


# ---------------------------------------------------------------------------
# Skills processing (canonical case + synonym dedupe + drop tags + cap)
# ---------------------------------------------------------------------------
def _process_skill_list(items: List[str], rules: Dict[str, Any], jd_text: str,
                        cap: int, location: str) -> Tuple[List[str], List[Finding]]:
    findings: List[Finding] = []
    skills_cfg = rules.get("skills") or {}
    jd_low = (jd_text or "").lower()

    drop_frag = [f.lower() for f in (skills_cfg.get("drop_orphan_fragments") or [])]
    drop_tags = [t.lower() for t in (skills_cfg.get("drop_tags") or [])]
    drop_unless_jd = bool(skills_cfg.get("drop_tags_unless_jd_mentions"))

    # variant -> canonical
    variant_map: Dict[str, str] = {}
    if skills_cfg.get("apply_synonym_map"):
        for entry in rules.get("synonym_map") or []:
            canon = entry.get("canonical")
            for v in entry.get("variants") or []:
                variant_map[str(v).strip().lower()] = canon

    out: List[str] = []
    seen_canon: set = set()
    for raw in items:
        item = str(raw).strip()
        if not item:
            continue

        # drop orphan fragments
        for frag in drop_frag:
            if frag in item.lower():
                item = re.sub(re.escape(frag), "", item, flags=re.IGNORECASE).strip(" -,/")
        if not item:
            continue

        # study/learning tags
        dropped = False
        for tag in drop_tags:
            if tag in item.lower():
                base = re.sub(re.escape(tag), "", item, flags=re.IGNORECASE).strip(" -,/")
                if drop_unless_jd and base and base.lower() in jd_low:
                    item = base  # JD wants it: keep, strip the tag
                    findings.append(Finding("drop_study_tag", AUTOFIX, location,
                                            f"stripped study tag (JD-relevant): {raw}", str(raw), item))
                else:
                    findings.append(Finding("drop_study_tag", AUTOFIX, location,
                                            f"dropped padded study tool: {raw}", str(raw), ""))
                    dropped = True
                break
        if dropped:
            continue

        # synonym canonicalization (canonical value is authoritative casing)
        from_synonym = False
        if item.lower() in variant_map:
            canon = variant_map[item.lower()]
            if canon and canon != item:
                findings.append(Finding("synonym_dedupe", AUTOFIX, location,
                                        f"merged '{item}' -> '{canon}'", item, canon))
            item = canon
            from_synonym = True

        # canonical casing (skip when value came from the synonym map — that form
        # is already correctly cased and re-casing can corrupt slash/space values)
        if not from_synonym and skills_cfg.get("enforce_canonical_casing"):
            cased = _title_case_token(item, rules)
            if cased != item:
                findings.append(Finding("casing", AUTOFIX, location,
                                        f"recased '{item}' -> '{cased}'", item, cased))
                item = cased

        # dedupe (case-insensitive)
        if skills_cfg.get("forbid_duplicates", True):
            ckey = item.lower()
            if ckey in seen_canon:
                findings.append(Finding("synonym_dedupe", AUTOFIX, location,
                                        f"dropped duplicate skill: {item}", item, ""))
                continue
            seen_canon.add(ckey)
        out.append(item)

    # cap
    if cap and len(out) > cap:
        findings.append(Finding("skills_cap", AUTOFIX, location,
                                f"capped skills {len(out)} -> {cap}", str(len(out)), str(cap)))
        out = out[:cap]
    return out, findings


# ---------------------------------------------------------------------------
# Summary processing
# ---------------------------------------------------------------------------
_COMMON_VERBS = ("support", "supported", "manage", "managed", "resolve", "resolved",
                 "troubleshoot", "build", "built", "document", "documented", "experience",
                 "experienced", "provide", "provided", "maintain", "maintained", "lead",
                 "led", "coordinate", "coordinated", "deliver", "delivered", "assess",
                 "communicat", "creating", "create", "created", "is ", "with ")


def _process_summary(summary: str, rules: Dict[str, Any], job_title: str,
                     location: str) -> Tuple[str, List[Finding]]:
    findings: List[Finding] = []
    cfg = rules.get("summary") or {}
    s = (summary or "").strip()
    if not s:
        return s, findings

    s, autonotes = _apply_text_autofixes(s, rules)
    for nt in autonotes:
        findings.append(Finding("phrase_replacement", AUTOFIX, location, nt))

    # comma-salad / verbless malformed summary (block)
    if cfg.get("require_no_comma_salad", True):
        slashes = s.count("/")
        commas = s.count(",")
        low = s.lower()
        has_verb = any(v in low for v in _COMMON_VERBS)
        if slashes >= 4 or (commas >= 5 and not has_verb):
            findings.append(Finding("comma_salad_summary",
                                    _sev(rules, "comma_salad_summary", "malformed_field", BLOCK),
                                    location,
                                    "summary reads as a verbless keyword/slash salad; regenerate",
                                    original=s[:120]))

    # must open with target title
    if cfg.get("must_open_with_target_title") and job_title:
        short_title = job_title.split("-")[0].strip() or job_title
        lead = (cfg.get("title_lead_template") or "{title} candidate").format(title=short_title).strip()
        lead_full = (cfg.get("title_lead_template") or "{title} candidate").format(title=job_title).strip()
        low = s.lower()
        title_starts = low.startswith(job_title.lower()) or low.startswith(short_title.lower())
        candidate_opener = (
            low.startswith(lead.lower())
            or low.startswith(lead_full.lower())
            or low.startswith(f"{short_title.lower()} candidate")
        )
        if title_starts and not candidate_opener:
            rest = s
            if low.startswith(job_title.lower()):
                rest = s[len(job_title) :].lstrip(" ,–-")
            elif low.startswith(short_title.lower()):
                rest = s[len(short_title) :].lstrip(" ,–-")
            rest = re.sub(
                r"^(?:remote\s+)?(?:focused on|with focus on|provides?|specializ\w+\s+in)\s+",
                "",
                rest,
                flags=re.IGNORECASE,
            ).strip()
            if rest and rest[0].islower():
                rest = rest[0].upper() + rest[1:]
            if rest:
                rest_body = rest[0].lower() + rest[1:]
                # Trim duplicate connectives so we don't produce "with hands-on with ...".
                rest_body = re.sub(
                    r"^(?:with\s+hands-on\s+|with\s+|hands-on\s+)",
                    "",
                    rest_body,
                    flags=re.IGNORECASE,
                )
                new = f"{lead} with hands-on {rest_body}"
            else:
                new = lead
            findings.append(Finding("summary_title_lead", AUTOFIX, location,
                                    f"retitled pasted JD summary -> '{lead} ...'", s[:60], new[:60]))
            s = new
            low = s.lower()
        if not (low.startswith(lead.lower()) or low.startswith(short_title.lower())):
            replaced = False
            for opener in cfg.get("forbidden_openers") or []:
                op = str(opener).lower()
                if low.startswith(op):
                    rest = s[len(opener):].lstrip(" ,–-")
                    new = f"{lead} {rest}".strip()
                    findings.append(Finding("summary_title_lead", AUTOFIX, location,
                                            f"retitled summary opener -> '{lead} ...'", s[:60], new[:60]))
                    s = new
                    replaced = True
                    break
            if not replaced:
                if low.startswith(("with ", "experienced ", "skilled ", "hands-on")):
                    new = f"{lead} {s[:1].lower() + s[1:]}"
                    findings.append(Finding("summary_title_lead", AUTOFIX, location,
                                            f"prepended target title -> '{lead} ...'", s[:60], new[:60]))
                    s = new
                else:
                    findings.append(Finding("summary_title_lead",
                                            _sev(rules, "summary_title_lead", "summary_title_lead", WARN),
                                            location, "summary does not open with the target title",
                                            original=s[:80]))

    # length
    max_chars = cfg.get("max_chars")
    if max_chars and len(s) > max_chars:
        findings.append(Finding("summary_too_long", WARN, location,
                                f"summary {len(s)} chars > {max_chars}", original=str(len(s))))

    findings.extend(_scan_banned(s, rules, location))
    return s, findings


# ---------------------------------------------------------------------------
# Bullet processing
# ---------------------------------------------------------------------------
def _process_bullets(bullets: List[str], rules: Dict[str, Any], location_prefix: str
                     ) -> Tuple[List[str], List[Finding]]:
    findings: List[Finding] = []
    cfg = rules.get("bullets") or {}
    weak_starts = [w.lower() for w in (rules.get("weak_bullet_starts") or [])]

    expanded: List[str] = []
    for b in bullets:
        text = str(b).strip()
        if not text:
            continue
        loc = f"{location_prefix}[{len(expanded)}]"
        # autofix text
        text, autonotes = _apply_text_autofixes(text, rules)
        for nt in autonotes:
            findings.append(Finding("phrase_replacement", AUTOFIX, loc, nt))

        # semicolon -> split into separate bullets (one idea per bullet)
        if cfg.get("forbid_semicolon") and ";" in text:
            parts = [p.strip().rstrip(".") for p in text.split(";") if p.strip()]
            if len(parts) > 1:
                parts = [p[:1].upper() + p[1:] if p else p for p in parts]
                findings.append(Finding("semicolon_bullet", AUTOFIX, loc,
                                        f"split semicolon bullet into {len(parts)} bullets",
                                        original=text[:80]))
                expanded.extend(parts)
                continue
        expanded.append(text)

    out: List[str] = []
    for idx, text in enumerate(expanded):
        loc = f"{location_prefix}[{idx}]"

        # length
        if cfg.get("max_chars") and len(text) > cfg["max_chars"]:
            findings.append(Finding("bullet_too_long", WARN, loc,
                                    f"bullet {len(text)} chars > {cfg['max_chars']}", original=text[:60]))
        if cfg.get("max_words") and len(text.split()) > cfg["max_words"]:
            findings.append(Finding("bullet_too_long", WARN, loc,
                                    f"bullet {len(text.split())} words > {cfg['max_words']}", original=text[:60]))

        low = text.lower()
        # first person in resume bullet
        if cfg.get("forbid_first_person") and low.startswith(_PRONOUN_STARTS):
            findings.append(Finding("first_person_in_resume", WARN, loc,
                                    "resume bullet uses first person", original=text[:60]))
        # weak opener
        for w in weak_starts:
            if low.startswith(w):
                findings.append(Finding("weak_bullet_start", WARN, loc,
                                        f"weak/passive opener: '{w}'", original=text[:60]))
                break
        # action-verb start (soft: only flag clear non-verb starts)
        if cfg.get("require_action_verb_start"):
            toks = text.split()
            first = re.sub(r"[^a-zA-Z]", "", toks[0]) if toks else ""
            if first and (first.lower() in ("the", "a", "an") or low.startswith(_PRONOUN_STARTS)):
                findings.append(Finding("weak_bullet_start", WARN, loc,
                                        "bullet should start with an action verb", original=text[:60]))
        # trailing conjunction
        if cfg.get("forbid_trailing_conjunction") and re.search(r"\b(and|or|to|with)\s*$", text, re.IGNORECASE):
            findings.append(Finding("bullet_trailing_conjunction", WARN, loc,
                                    "bullet ends in a dangling conjunction", original=text[-30:]))
        # slash-salad
        if cfg.get("max_slashes_per_line") is not None and text.count("/") > cfg["max_slashes_per_line"]:
            findings.append(Finding("slash_salad", WARN, loc,
                                    f"{text.count('/')} slashes in one bullet", original=text[:60]))

        findings.extend(_scan_banned(text, rules, loc))
        out.append(text)

    # count bounds
    if cfg.get("max_per_role") and len(out) > cfg["max_per_role"]:
        findings.append(Finding("bullet_count", WARN, location_prefix,
                                f"{len(out)} bullets > max {cfg['max_per_role']}", original=str(len(out))))
    if cfg.get("min_per_role") and 0 < len(out) < cfg["min_per_role"]:
        findings.append(Finding("bullet_count", WARN, location_prefix,
                                f"{len(out)} bullets < min {cfg['min_per_role']}", original=str(len(out))))
    return out, findings


# ---------------------------------------------------------------------------
# Parser/glyph scan (works on the JSON pre-render)
# ---------------------------------------------------------------------------
def _scan_parser(content: Dict[str, Any], rules: Dict[str, Any]) -> List[Finding]:
    findings: List[Finding] = []
    cfg = rules.get("parser") or {}
    blob = json.dumps(content, ensure_ascii=False)
    for g in cfg.get("forbid_glyphs") or []:
        if g and g in blob:
            findings.append(Finding("glyph_garbage", _sev(rules, "glyph_garbage", "glyph_garbage", BLOCK),
                                    "content", f"garbage glyph present: {g!r}"))
    if cfg.get("forbid_db_date_format") and re.search(r"\b\d{4}-\d{2}\b", blob):
        findings.append(Finding("db_date_leak", WARN, "content",
                                "DB-style YYYY-MM date in candidate-facing text"))
    if cfg.get("require_company_on_each_experience") or cfg.get("require_title_on_each_experience"):
        exps = content.get("experience") if isinstance(content.get("experience"), list) else []
        for i, e in enumerate(exps):
            if not isinstance(e, dict):
                continue
            if cfg.get("require_company_on_each_experience") and not str(e.get("company") or "").strip():
                findings.append(Finding("missing_company", BLOCK, f"experience[{i}]", "experience missing company"))
            if cfg.get("require_title_on_each_experience") and not str(e.get("title") or "").strip():
                findings.append(Finding("missing_title", BLOCK, f"experience[{i}]", "experience missing title"))
    return findings


# ---------------------------------------------------------------------------
# Public: resume
# ---------------------------------------------------------------------------
def lint_resume(content: Dict[str, Any], *, job_title: str = "", jd_text: str = "",
                rules: Optional[Dict[str, Any]] = None) -> LintResult:
    rules = rules or load_rules()
    try:
        working = copy.deepcopy(content)
        findings: List[Finding] = []

        if working.get("error"):
            return LintResult(content=working, findings=[], notes=[], penalty=0.0)

        # summary
        if "summary" in working:
            new_summary, f = _process_summary(str(working.get("summary") or ""), rules, job_title, "summary")
            working["summary"] = new_summary
            findings.extend(f)

        # experience bullets
        exps = working.get("experience")
        if isinstance(exps, list):
            for i, exp in enumerate(exps):
                if not isinstance(exp, dict):
                    continue
                bullets = exp.get("bullets")
                if isinstance(bullets, list):
                    new_b, f = _process_bullets(bullets, rules, f"experience[{i}].bullets")
                    exp["bullets"] = new_b
                    findings.extend(f)

        # project descriptions (text autofix + banned scan only)
        projs = working.get("projects")
        if isinstance(projs, list):
            for i, p in enumerate(projs):
                if not isinstance(p, dict):
                    continue
                for fld in ("description", "impact"):
                    val = str(p.get(fld) or "")
                    if not val:
                        continue
                    new_val, autonotes = _apply_text_autofixes(val, rules)
                    for nt in autonotes:
                        findings.append(Finding("phrase_replacement", AUTOFIX, f"projects[{i}].{fld}", nt))
                    findings.extend(_scan_banned(new_val, rules, f"projects[{i}].{fld}"))
                    p[fld] = new_val

        # skills
        sk = working.get("skills")
        skills_cfg = rules.get("skills") or {}
        if isinstance(sk, dict):
            if isinstance(sk.get("technical"), list):
                new_t, f = _process_skill_list(sk["technical"], rules, jd_text,
                                               skills_cfg.get("technical_cap", 22), "skills.technical")
                sk["technical"] = new_t
                findings.extend(f)
            if isinstance(sk.get("soft"), list):
                new_s, f = _process_skill_list(sk["soft"], rules, jd_text,
                                               skills_cfg.get("soft_cap", 12), "skills.soft")
                sk["soft"] = new_s
                findings.extend(f)
        elif isinstance(sk, list):
            new_t, f = _process_skill_list(sk, rules, jd_text, skills_cfg.get("technical_cap", 22), "skills")
            working["skills"] = new_t
            findings.extend(f)

        # parser/glyph scan
        findings.extend(_scan_parser(working, rules))

        notes = [f.as_note() for f in findings]
        return LintResult(content=working, findings=findings, notes=notes,
                          penalty=presentation_penalty(findings, rules))
    except Exception as exc:  # never crash a build
        logger.warning("presentation_linter.lint_resume failed: %s", exc)
        return LintResult(content=content, findings=[],
                          notes=[f"presentation: linter error (skipped): {exc}"], penalty=0.0)


# ---------------------------------------------------------------------------
# Public: cover letter
# ---------------------------------------------------------------------------
def lint_cover_letter(content: Dict[str, Any], *, company: str = "", role: str = "",
                      jd_text: str = "", rules: Optional[Dict[str, Any]] = None) -> LintResult:
    rules = rules or load_rules()
    try:
        working = copy.deepcopy(content)
        findings: List[Finding] = []
        cfg = rules.get("cover_letter") or {}

        if working.get("error"):
            return LintResult(content=working, findings=[], notes=[], penalty=0.0)

        def _fix_field(name: str) -> str:
            val = str(working.get(name) or "")
            if not val:
                return val
            new_val, autonotes = _apply_text_autofixes(val, rules)
            for nt in autonotes:
                findings.append(Finding("phrase_replacement", AUTOFIX, name, nt))
            findings.extend(_scan_banned(new_val, rules, name))
            return new_val

        if "opening" in working:
            working["opening"] = _fix_field("opening")
        if "closing" in working:
            working["closing"] = _fix_field("closing")

        bodies = working.get("body_paragraphs")
        if isinstance(bodies, list):
            new_bodies = []
            for i, para in enumerate(bodies):
                val = str(para or "")
                new_val, autonotes = _apply_text_autofixes(val, rules)
                for nt in autonotes:
                    findings.append(Finding("phrase_replacement", AUTOFIX, f"body_paragraphs[{i}]", nt))
                findings.extend(_scan_banned(new_val, rules, f"body_paragraphs[{i}]"))
                new_bodies.append(new_val)
            working["body_paragraphs"] = new_bodies
            if cfg.get("max_body_paragraphs") and len(new_bodies) > cfg["max_body_paragraphs"]:
                findings.append(Finding("cl_paragraph_count", WARN, "body_paragraphs",
                                        f"{len(new_bodies)} body paragraphs > {cfg['max_body_paragraphs']}"))
            if cfg.get("min_body_paragraphs") and 0 < len(new_bodies) < cfg["min_body_paragraphs"]:
                findings.append(Finding("cl_paragraph_count", WARN, "body_paragraphs",
                                        f"{len(new_bodies)} body paragraphs < {cfg['min_body_paragraphs']}"))

        # whole-letter checks
        full = " ".join([
            str(working.get("opening") or ""),
            *[str(p) for p in (working.get("body_paragraphs") or [])],
            str(working.get("closing") or ""),
        ])
        full_low = full.lower()
        words = len(full.split())
        if cfg.get("min_words") and words < cfg["min_words"]:
            findings.append(Finding("cl_length", WARN, "cover_letter",
                                    f"{words} words < min {cfg['min_words']}"))
        if cfg.get("max_words") and words > cfg["max_words"]:
            findings.append(Finding("cl_length", WARN, "cover_letter",
                                    f"{words} words > max {cfg['max_words']}"))

        if cfg.get("require_company_name") and company:
            if company.lower() not in full_low:
                findings.append(Finding("cl_missing_company",
                                        _sev(rules, "cl_missing_company", "cl_missing_company", BLOCK),
                                        "cover_letter", f"cover letter never names the company '{company}'"))
        if cfg.get("require_role_name") and role:
            tokens = [t for t in re.split(r"\W+", role.lower()) if len(t) > 3]
            if tokens and not any(t in full_low for t in tokens):
                findings.append(Finding("cl_missing_role",
                                        _sev(rules, "cl_missing_role", "cl_missing_role", BLOCK),
                                        "cover_letter", f"cover letter never names the role '{role}'"))

        if cfg.get("forbid_first_person_overload"):
            sents = [s for s in re.split(r"(?<=[.!?])\s+", full) if s.strip()]
            if sents:
                i_starts = sum(1 for s in sents if s.strip().lower().startswith(("i ", "i'")))
                if i_starts / len(sents) > 0.4:
                    findings.append(Finding("cl_first_person_overload", WARN, "cover_letter",
                                            f"{i_starts}/{len(sents)} sentences start with 'I'"))

        notes = [f.as_note() for f in findings]
        return LintResult(content=working, findings=findings, notes=notes,
                          penalty=presentation_penalty(findings, rules))
    except Exception as exc:
        logger.warning("presentation_linter.lint_cover_letter failed: %s", exc)
        return LintResult(content=content, findings=[],
                          notes=[f"presentation: linter error (skipped): {exc}"], penalty=0.0)


# ---------------------------------------------------------------------------
# Public: cross-document consistency
# ---------------------------------------------------------------------------
def _extract_yoe(text: str) -> Optional[str]:
    m = re.search(r"(\d{1,2})\s*\+?\s*years?", (text or "").lower())
    return m.group(1) if m else None


def cross_document_consistency(resume: Dict[str, Any], cover_letter: Dict[str, Any], *,
                               role: str = "", company: str = "",
                               rules: Optional[Dict[str, Any]] = None) -> List[Finding]:
    rules = rules or load_rules()
    cfg = rules.get("cross_document") or {}
    findings: List[Finding] = []
    try:
        resume_text = " ".join([
            str(resume.get("summary") or ""),
            *[" ".join(str(b) for b in (e.get("bullets") or []))
              for e in (resume.get("experience") or []) if isinstance(e, dict)],
        ])
        cl_text = " ".join([
            str(cover_letter.get("opening") or ""),
            *[str(p) for p in (cover_letter.get("body_paragraphs") or [])],
            str(cover_letter.get("closing") or ""),
        ])

        if cfg.get("check_years_experience_match"):
            ry, cy = _extract_yoe(resume_text), _extract_yoe(cl_text)
            if ry and cy and ry != cy:
                findings.append(Finding("cross_doc_yoe_mismatch", WARN, "cross_document",
                                        f"resume says {ry}+ yrs, cover letter says {cy}+ yrs"))

        if cfg.get("check_target_title_match") and role:
            tokens = [t for t in re.split(r"\W+", role.lower()) if len(t) > 3]
            if tokens and not any(t in cl_text.lower() for t in tokens):
                findings.append(Finding("cross_doc_title_mismatch", WARN, "cross_document",
                                        f"cover letter does not reference target role '{role}'"))

        # resume bullets pasted verbatim into the cover letter
        cl_norm = re.sub(r"\s+", " ", cl_text.lower())
        for e in (resume.get("experience") or []):
            if not isinstance(e, dict):
                continue
            hit = False
            for b in (e.get("bullets") or []):
                norm = re.sub(r"\s+", " ", str(b).strip().lower())
                if len(norm) > 40 and norm in cl_norm:
                    findings.append(Finding("cl_resume_verbatim", WARN, "cross_document",
                                            "cover letter repeats a resume bullet verbatim",
                                            original=str(b)[:60]))
                    hit = True
                    break
            if hit:
                break
    except Exception as exc:
        logger.warning("presentation_linter.cross_document_consistency failed: %s", exc)
    return findings


# ---------------------------------------------------------------------------
# Penalty
# ---------------------------------------------------------------------------
def presentation_penalty(findings: List[Finding], rules: Optional[Dict[str, Any]] = None) -> float:
    rules = rules or load_rules()
    p = rules.get("penalty") or {}
    per_warn = float(p.get("per_warn", 0.75))
    per_block = float(p.get("per_block", 3.0))
    max_total = float(p.get("max_total", 15.0))
    total = 0.0
    for f in findings:
        if f.severity == WARN:
            total += per_warn
        elif f.severity == BLOCK:
            total += per_block
        # autofix contributes 0 — it's already fixed
    return round(min(total, max_total), 2)
