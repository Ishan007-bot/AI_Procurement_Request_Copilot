"""Untrusted business-data handling (policy section 9).

Request text, vendor notes, catalog notes and API text are data, never
instructions. This module:
  * detects text that tries to instruct the system (prompt injection) and text
    that asserts approvals that do not exist in any approval record;
  * wraps untrusted text in delimiters before it is shown to a model.

Detection only surfaces what it finds. It never changes approvals or flags
downward: the policy floor is computed independently of this text.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from pydantic import BaseModel

INJECTION = "prompt_injection_detected"
APPROVAL_CLAIM = "unverified_approval_claim"

_CONTROLS = r"(?:rules?|instructions?|polic(?:y|ies)|controls?|guidelines?|procedures?|checks?|guardrails?|restrictions?)"
_REVIEWS = r"(?:security|privacy|legal|procurement|finance|compliance|review|reviews|approvals?|polic(?:y|ies)|controls?|checks?)"

# (kind, label, pattern)
PATTERNS: list[tuple[str, str, re.Pattern]] = [
    (INJECTION, "instruction override",
     re.compile(rf"\b(?:ignore|disregard|forget|override|overrule|bypass)\b(?:\s+\w+){{0,3}}?\s+{_CONTROLS}\b")),
    (INJECTION, "control bypass",
     re.compile(rf"\b(?:bypass|skip|circumvent|waive|disable|turn off)\b(?:\s+(?:the|all|any|this|these))?\s+{_REVIEWS}\b")),
    (INJECTION, "approval command",
     re.compile(r"\b(?:auto[- ]?approve|approve\s+(?:it|this|the request|this request|immediately|now|automatically|right away)"
                r"(?:\s+(?:immediately|now|automatically|right away))?)\b")),
    (INJECTION, "decision override",
     re.compile(r"\b(?:treat|mark|consider|classify|record|set)\s+(?:this|it|the request|this request|the vendor|the risk)?\s*"
                r"(?:as|to)\s+(?:already\s+)?(?:approved|pre[- ]?approved|\w+[- ]approved|low[- ]risk|compliant|exempt|safe)\b")),
    (INJECTION, "no-review assertion",
     re.compile(rf"\bno\s+(?:need\s+for\s+|further\s+)?(?:{_REVIEWS}\s+){{1,2}}(?:is\s+|are\s+)?(?:needed|required|necessary)\b")),
    (INJECTION, "role or prompt manipulation",
     re.compile(r"\b(?:you are now|act as (?:an?|the) |new instructions|system prompt|developer prompt|jailbreak|do anything now|"
                r"as an ai(?: language model)?,? you must)\b")),
    (INJECTION, "secret exfiltration",
     re.compile(r"\b(?:reveal|print|show|expose|leak|output|send)\b(?:\s+\w+){0,3}?\s+(?:system prompt|api[ _-]?keys?|secrets?|"
                r"credentials?|passwords?|tokens?)\b")),
    (INJECTION, "delimiter or role tag",
     re.compile(r"</?\s*(?:system|assistant|instructions?|untrusted_data)\s*>|^\s*#{2,}\s*(?:system|instructions?)\b", re.MULTILINE)),
    (APPROVAL_CLAIM, "claimed approval",
     re.compile(r"\b(?:pre[- ]?approved|already approved|(?:cfo|ceo|cto|coo|vp|director|finance|security|legal|privacy|procurement|manager)"
                r"[- ](?:has\s+)?(?:already\s+)?(?:approved|signed[- ]off))\b")),
]

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
EXCERPT_CHARS = 140


class UntrustedTextFinding(BaseModel):
    kind: str           # prompt_injection_detected | unverified_approval_claim
    label: str
    source: str         # tool whose data contained it
    field: str
    excerpt: str


def normalize_text(text: object) -> str:
    """NFKC-fold, drop zero-width characters and collapse whitespace so obfuscation doesn't hide matches."""
    folded = unicodedata.normalize("NFKC", str(text or "")).translate(ZERO_WIDTH)
    return re.sub(r"\s+", " ", folded).strip()


def _excerpt(text: str, start: int, end: int) -> str:
    left = max(0, start - 40)
    right = min(len(text), max(end + 40, left + EXCERPT_CHARS))
    snippet = text[left:right]
    return ("..." if left else "") + snippet + ("..." if right < len(text) else "")


def scan_text(text: object, *, source: str, field: str) -> list[UntrustedTextFinding]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    lowered = normalized.lower()
    findings: list[UntrustedTextFinding] = []
    seen: set[tuple[str, str]] = set()
    for kind, label, pattern in PATTERNS:
        match = pattern.search(lowered)
        if match and (kind, label) not in seen:
            seen.add((kind, label))
            findings.append(UntrustedTextFinding(kind=kind, label=label, source=source, field=field,
                                                 excerpt=_excerpt(normalized, match.start(), match.end())))
    return findings


def scan_fields(items: Iterable[tuple[str, str, Any]]) -> list[UntrustedTextFinding]:
    """Scan (source, field, value) triples; list values are scanned item by item."""
    findings: list[UntrustedTextFinding] = []
    for source, field, value in items:
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            if isinstance(item, str):
                findings.extend(scan_text(item, source=source, field=field))
    return findings


def summarize_findings(findings: Iterable[UntrustedTextFinding]) -> list[dict]:
    """Compact form for tool outputs: one entry per (kind, field), without repeating the text itself."""
    grouped: dict[tuple[str, str], dict] = {}
    for f in findings:
        entry = grouped.setdefault((f.kind, f.field), {"kind": f.kind, "field": f.field, "source": f.source, "patterns": []})
        entry["patterns"].append(f.label)
    return list(grouped.values())


def wrap_untrusted(field: str, text: object) -> str:
    """Delimit untrusted text for a model prompt. Delimiter look-alikes inside the text are neutralised."""
    body = normalize_text(text)
    body = re.sub(r"<\s*(/?)\s*untrusted_data", r"[\1untrusted_data", body, flags=re.IGNORECASE)
    safe_field = re.sub(r"[^A-Za-z0-9_.\-]", "_", field)
    return f'<untrusted_data field="{safe_field}">{body}</untrusted_data>'
