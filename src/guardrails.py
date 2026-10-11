"""Deterministic guardrails between an agent's draft and the final decision.

The policy engine's result is the floor. The model may add approvals, flags and
missing-information items; it can never remove one. Every correction is
recorded in telemetry as "<category>: <detail>" so the evaluation can measure
how often each architecture needed the code to step in.

Categories: approval_added, flag_added, missing_added, missing_dropped,
evidence_dropped, evidence_added, recommendation_changed, approval_unrecognized.
"""
from __future__ import annotations

import re

from src.contracts import DraftDecision, EvidenceItem, ProcurementDecision
from src.data_access import to_number
from src.injection import APPROVAL_CLAIM, INJECTION
from src.policy_engine import (
    APPROVAL_ORDER, RECOMMENDATION_LABELS, RECOMMENDATION_PRECEDENCE, PolicyResult,
    is_unknown, minimum_recommendation, next_step_for,
)
from src.telemetry import RunTelemetryCounter

GUARDRAIL_SOURCE = "policy_guardrail"

APPROVAL_SYNONYMS = {
    "manager": "Manager", "line manager": "Manager", "direct manager": "Manager", "people manager": "Manager",
    "department head": "Department Head", "dept head": "Department Head", "head of department": "Department Head",
    "department director": "Department Head",
    "procurement": "Procurement", "procurement team": "Procurement",
    "finance": "Finance", "finance team": "Finance", "budget owner": "Finance",
    "cfo": "CFO", "chief financial officer": "CFO",
    "security": "Security", "infosec": "Security", "information security": "Security", "security team": "Security",
    "privacy": "Privacy", "data protection": "Privacy", "dpo": "Privacy", "privacy team": "Privacy",
    "legal": "Legal", "legal team": "Legal", "counsel": "Legal",
}

# Engine findings always carried into the final evidence (when the agent omitted that source).
KEY_RULES = ["threshold", "budget", "vendor_risk", "vendor_conflict", "assessment_expiry", "restricted_use", "untrusted_content",
             "required_information"]

# Missing-information items that are contradicted when the request field is actually present.
# Only phrases that NAME the field: an earlier, looser version ("data access", "users") also dropped real
# follow-up questions such as "what personal data would the vendor process given its data access?".
FIELD_PATTERNS = {
    "annual_cost_usd": re.compile(r"\b(annual cost|annual spend|cost estimate|annual estimate|pricing|price)\b", re.I),
    "user_count": re.compile(r"\b(number of (users|seats|licen[cs]es)|(user|seat|licen[cs]e) count|"
                             r"how many (users|seats|licen[cs]es))\b", re.I),
    "data_access_level": re.compile(r"\b(data[- ]access level|data classification|level of data access)\b", re.I),
}


def normalize_approval(name: str) -> str | None:
    key = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", str(name).lower())).strip()
    if key in APPROVAL_SYNONYMS:
        return APPROVAL_SYNONYMS[key]
    for canonical in APPROVAL_ORDER:
        if key == canonical.lower():
            return canonical
    # e.g. "Department Head (David Chen)" -> leading phrase match
    for synonym in sorted(APPROVAL_SYNONYMS, key=len, reverse=True):
        if key.startswith(synonym + " "):
            return APPROVAL_SYNONYMS[synonym]
    return None


def normalize_flag(flag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(flag).strip().lower()).strip("_")


def match_label(text: str) -> str | None:
    lowered = str(text).strip().lower()
    for label in RECOMMENDATION_LABELS:
        if lowered.startswith(label.lower()):
            return label
    return None


def _field_present(request: dict, field: str) -> bool:
    value = request.get(field)
    if field in ("annual_cost_usd",):
        number = to_number(value)
        return number is not None and number >= 0
    if field == "user_count":
        number = to_number(value)
        return number is not None and number > 0
    return not is_unknown(value)


def apply_guardrails(draft: DraftDecision, policy: PolicyResult, request: dict, tools_called: set[str],
                     telemetry: RunTelemetryCounter) -> ProcurementDecision:
    telemetry.record_guardrail_check()
    note = telemetry.record_override

    # 1. approvals: canonical names, union with the floor
    approvals: set[str] = set()
    for name in draft.required_approvals:
        canonical = normalize_approval(name)
        if canonical:
            approvals.add(canonical)
        else:
            note(f"approval_unrecognized: '{name}'")
    for role in policy.required_approvals:
        if role not in approvals:
            note(f"approval_added: {role}")
            approvals.add(role)
    ordered_approvals = [r for r in APPROVAL_ORDER if r in approvals]

    # 2. risk flags: snake_case, union with the floor
    flags: list[str] = []
    for flag in draft.risk_flags:
        normalized = normalize_flag(flag)
        if normalized and normalized not in flags:
            flags.append(normalized)
    for flag in policy.risk_flags:
        if flag not in flags:
            note(f"flag_added: {flag}")
            flags.append(flag)

    # 3. missing information: drop items contradicted by the request, union with the floor
    missing: list[str] = []
    open_questions: list[str] = []
    for item in draft.missing_information:
        text = str(item).strip()
        if not text or any(text.lower() == m.lower() for m in missing):
            continue
        contradicted = [f for f, pattern in FIELD_PATTERNS.items() if pattern.search(text) and _field_present(request, f)]
        floor_mentions = any(pattern.search(text) for f, pattern in FIELD_PATTERNS.items() if not _field_present(request, f))
        if contradicted and not floor_mentions:
            note(f"missing_dropped: '{text}' ({', '.join(contradicted)} is present in the request)")
            continue
        if not policy.missing_information:
            # Section 1 is satisfied: the item is a reviewer follow-up, not missing request information.
            # Keep it visible as an open question instead of making a complete request look incomplete.
            note(f"missing_moved: '{text}' (request complete per section 1; kept as an open question)")
            open_questions.append(text)
            continue
        missing.append(text)
    for item in policy.missing_information:
        covered = any(item.lower() == m.lower() for m in missing)
        if not covered:
            field = next((f for f, p in FIELD_PATTERNS.items() if p.search(item)), None)
            covered = field is not None and any(FIELD_PATTERNS[field].search(m) for m in missing)
        if not covered:
            note(f"missing_added: {item}")
            missing.append(item)
    if missing and "missing_information" not in flags:
        flags.append("missing_information")
    if not missing and "missing_information" in flags:
        flags.remove("missing_information")
        note("flag_removed: missing_information (no required request information is missing)")

    # 4. evidence: keep only items from tools that actually ran; add key engine findings the agent omitted
    evidence: list[EvidenceItem] = []
    for item in draft.evidence:
        if item.source in tools_called and item.finding.strip():
            evidence.append(EvidenceItem(source=item.source, finding=item.finding.strip(), reference=item.reference))
        else:
            note(f"evidence_dropped: source '{item.source}' was not called in this run")
    cited_sources = {e.source for e in evidence}
    for finding in policy.findings:
        if finding.rule not in KEY_RULES or finding.source in cited_sources:
            continue
        if finding.rule == "required_information" and not policy.missing_information:
            continue  # "all fields present" adds nothing to the evidence
        evidence.append(EvidenceItem(source=GUARDRAIL_SOURCE, finding=finding.finding,
                                     reference=f"{finding.reference or '-'}; from {finding.source}"))
        note(f"evidence_added: {finding.rule}")
    for role in ("Manager", "Department Head"):
        if role in ordered_approvals and role in policy.approver_assignments:
            evidence.append(EvidenceItem(source=GUARDRAIL_SOURCE,
                                         finding=f"{role} approver: {policy.approver_assignments[role]}",
                                         reference="employees.csv reporting line"))

    # 5. recommendation: never less restrictive than the evidence allows
    label = match_label(draft.recommendation)
    # The clarification floor comes from the policy's required request fields (section 1) only. Follow-up
    # questions the model adds (e.g. "confirm the vendor's assessment status") are kept in the output, but must
    # not turn a correct "Manual review" into "Request clarification" (found in the evaluation, PUB-06).
    floor_label = minimum_recommendation(set(flags) - {"missing_information"} | set(policy.risk_flags),
                                         ordered_approvals, policy.missing_information)
    if label is None:
        note(f"recommendation_changed: unrecognised '{draft.recommendation}' -> {floor_label}")
        label = floor_label
    elif RECOMMENDATION_PRECEDENCE.index(label) > RECOMMENDATION_PRECEDENCE.index(floor_label):
        note(f"recommendation_changed: {label} -> {floor_label}")
        label = floor_label
    changed = label != match_label(draft.recommendation)
    rationale = draft.rationale.strip()
    recommendation = f"{label}: {rationale}" if rationale and not changed else (
        f"{label}: evidence requires this outcome (adjusted by policy guardrails)" if changed else label)

    # 6. next step: the agent's, unless the label changed; always carry the untrusted-content note
    next_step = draft.next_step.strip()
    if changed or not next_step:
        next_step = next_step_for(label, ordered_approvals, missing, set(flags))
    if open_questions:
        next_step += " Open questions for reviewers: " + "; ".join(open_questions[:5]) + "."
    if {INJECTION, APPROVAL_CLAIM} & set(flags) and "ignored" not in next_step.lower():
        next_step += (" Reviewer note: embedded instructions or approval claims in the request data were ignored; "
                      "every listed approval is still required.")

    return ProcurementDecision(
        request_id=policy.request_id or str(request.get("request_id") or ""),
        recommendation=recommendation,
        evidence=evidence,
        required_approvals=ordered_approvals,
        missing_information=missing,
        risk_flags=flags,
        next_step=next_step,
        human_review_required=True,
        telemetry=telemetry.to_contract(),
    )


def fallback_decision(policy: PolicyResult, telemetry: RunTelemetryCounter, reason: str) -> ProcurementDecision:
    """Rules-only decision when no model is usable. Conservative by construction."""
    telemetry.record_guardrail_check()
    flags = list(policy.risk_flags) + ["llm_unavailable"]
    label = policy.baseline_recommendation
    evidence = [EvidenceItem(source=GUARDRAIL_SOURCE, finding=f.finding, reference=f"{f.reference or '-'}; from {f.source}")
                for f in policy.findings]
    return ProcurementDecision(
        request_id=policy.request_id,
        recommendation=f"{label}: rules-only result; the AI assessment was unavailable ({reason.split(':')[0]})",
        evidence=evidence,
        required_approvals=policy.required_approvals,
        missing_information=policy.missing_information,
        risk_flags=flags,
        next_step=policy.baseline_next_step + " The AI assessment was unavailable; a reviewer must judge any existing-tool overlap manually.",
        human_review_required=True,
        telemetry=telemetry.to_contract(),
    )

