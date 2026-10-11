"""Scoring for the architecture comparison (used by evals/run_comparison.py).

Four quality dimensions per run (matching templates/evaluation_results_template.csv):
  correct_next_action      recommendation label is one of the gold labels
  grounded_evidence        every evidence source is a tool that ran (or the policy guardrail),
                           every $ amount / date in agent-written findings appears in tool output,
                           and there are at least 2 evidence items
  policy_followed          required approvals/flags/missing items present; forbidden ones absent
  human_escalation_correct human review flagged, required reviewers (Finance/Security/Privacy/Legal)
                           present, and the label is not less restrictive than the gold label

The same checks are also applied to the agent's raw draft (before guardrails) to
measure the architectures themselves rather than the shared safety net.
"""
from __future__ import annotations

import json
import re
from typing import Any

from src.guardrails import GUARDRAIL_SOURCE, match_label, normalize_approval, normalize_flag
from src.policy_engine import RECOMMENDATION_PRECEDENCE

REVIEW_ROLES = {"Finance", "Security", "Privacy", "Legal"}
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|K)?\b")
DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _rank(label: str | None) -> int:
    return RECOMMENDATION_PRECEDENCE.index(label) if label in RECOMMENDATION_PRECEDENCE else len(RECOMMENDATION_PRECEDENCE)


def evidence_corpus(tool_results: list[dict], request: dict) -> tuple[set[float], set[str]]:
    """All numbers and dates that tools returned (plus the request itself, which the intake tool returns)."""
    text = json.dumps([r.get("data") for r in tool_results if r.get("ok")], default=str) + json.dumps(request, default=str)
    numbers: set[float] = set()
    for raw in NUMBER.findall(text):
        try:
            numbers.add(round(float(raw.replace(",", "")), 2))
        except ValueError:
            continue
    return numbers, set(DATE.findall(text))


def ungrounded_figures(findings: list[str], numbers: set[float], dates: set[str]) -> list[str]:
    bad: list[str] = []
    for finding in findings:
        for amount, k in MONEY.findall(finding):
            value = round(float(amount.replace(",", "")) * (1000 if k else 1), 2)
            if value not in numbers:
                bad.append(f"${amount}{k}")
        bad.extend(d for d in DATE.findall(finding) if d not in dates)
    return bad


def _contains_group(items: list[str], group: list[str]) -> bool:
    lowered = [str(i).lower() for i in items]
    return any(token.lower() in item for token in group for item in lowered)


def policy_check(approvals: set[str], flags: set[str], missing: list[str], expect: dict) -> list[str]:
    problems: list[str] = []
    for role in expect.get("approvals_required", []):
        if role not in approvals:
            problems.append(f"missing approval {role}")
    for role in expect.get("approvals_forbidden", []):
        if role in approvals:
            problems.append(f"unexpected approval {role}")
    for flag in expect.get("flags_required", []):
        if flag not in flags:
            problems.append(f"missing flag {flag}")
    for flag in expect.get("flags_forbidden", []):
        if flag in flags:
            problems.append(f"unexpected flag {flag}")
    for group in expect.get("missing_required_groups", []):
        if not _contains_group(missing, group):
            problems.append(f"missing-info item absent ({'/'.join(group)})")
    if "max_missing" in expect and len(missing) > expect["max_missing"]:
        problems.append(f"too many missing-info items ({len(missing)})")
    return problems


def escalation_check(label: str | None, approvals: set[str], human_review: bool, expect: dict) -> list[str]:
    problems: list[str] = []
    if not human_review:
        problems.append("human review not required")
    for role in REVIEW_ROLES & set(expect.get("approvals_required", [])):
        if role not in approvals:
            problems.append(f"reviewer {role} not routed")
    if _rank(label) > max(_rank(gold) for gold in expect["labels"]):
        problems.append(f"less restrictive than gold ({label})")
    return problems


def score_decision(decision: dict, expect: dict, tool_results: list[dict], request: dict) -> dict:
    label = match_label(decision.get("recommendation", ""))
    approvals = {a for a in decision.get("required_approvals", [])}
    flags = {normalize_flag(f) for f in decision.get("risk_flags", [])}
    missing = list(decision.get("missing_information", []))
    evidence = decision.get("evidence", [])
    called = {r["name"] for r in tool_results if r.get("ok")}

    numbers, dates = evidence_corpus(tool_results, request)
    bad_sources = [e["source"] for e in evidence if e["source"] not in called and e["source"] != GUARDRAIL_SOURCE]
    agent_findings = [e["finding"] for e in evidence if e["source"] != GUARDRAIL_SOURCE]
    bad_figures = ungrounded_figures(agent_findings, numbers, dates)
    grounding_problems = ([f"evidence source not called: {s}" for s in bad_sources]
                          + [f"figure not in tool output: {f}" for f in bad_figures]
                          + (["fewer than 2 evidence items"] if len(evidence) < 2 else []))

    policy_problems = policy_check(approvals, flags, missing, expect)
    escalation_problems = escalation_check(label, approvals, bool(decision.get("human_review_required")), expect)
    return {
        "label": label,
        "correct_next_action": label in expect["labels"],
        "grounded_evidence": not grounding_problems,
        "policy_followed": not policy_problems,
        "human_escalation_correct": not escalation_problems,
        "agent_evidence_items": len(agent_findings),
        "guardrail_evidence_items": len(evidence) - len(agent_findings),
        "problems": ([f"label {label} not in {expect['labels']}"] if label not in expect["labels"] else [])
                    + grounding_problems + policy_problems + escalation_problems,
    }


def score_draft(draft: dict | None, expect: dict, policy_floor: dict) -> dict:
    """The agent's own output before guardrails: did it get the decision right by itself?"""
    if draft is None:
        return {"raw_label": None, "raw_correct_next_action": False, "raw_policy_followed": False,
                "raw_floor_coverage": 0.0, "raw_problems": ["no draft (fallback)"]}
    label = match_label(draft.get("recommendation", ""))
    approvals = {normalize_approval(a) for a in draft.get("required_approvals", [])} - {None}
    flags = {normalize_flag(f) for f in draft.get("risk_flags", [])}
    missing = list(draft.get("missing_information", []))
    problems = policy_check(approvals, flags, missing, expect)
    floor = set(policy_floor.get("required_approvals", [])) | {f"flag:{f}" for f in policy_floor.get("risk_flags", [])}
    got = approvals | {f"flag:{f}" for f in flags}
    coverage = len(floor & got) / len(floor) if floor else 1.0
    return {
        "raw_label": label,
        "raw_correct_next_action": label in expect["labels"],
        "raw_policy_followed": not problems,
        "raw_floor_coverage": round(coverage, 3),
        "raw_problems": problems,
    }


def override_counts(overrides: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in overrides:
        category = item.split(":", 1)[0]
        counts[category] = counts.get(category, 0) + 1
    return counts
