"""Deterministic procurement policy engine (data/procurement_policy.md).

Pure functions only: no file, network or model access. Given the evidence that
tools collected, it computes the *floor* of the decision: approvals, risk flags
and missing information the policy makes mandatory. The model can add to this
floor but never remove from it (see src/guardrails.py).

Every finding records which tool's data it came from (`source`) and the policy
section or record it rests on (`reference`), so evidence stays traceable.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from src.config import SECURITY_REVIEW_VALIDITY_DAYS
from src.data_access import to_number
from src.injection import APPROVAL_CLAIM, INJECTION, UntrustedTextFinding, scan_fields

# ---------------------------------------------------------------- vocabulary

APPROVAL_ORDER = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]

# Tool names (Phase 4) used as evidence sources.
SRC_REQUEST = "get_request_context"
SRC_BUDGET = "check_budget"
SRC_CATALOG = "search_software_catalog"
SRC_REGISTRY = "get_vendor_registry_record"
SRC_VENDOR_RISK = "get_vendor_risk"
SRC_POLICY = "evaluate_policy_rules"

PROCEED = "Proceed to approval routing"
USE_EXISTING = "Use existing tool / confirm need"
SPECIALIST = "Route for specialist review"
CLARIFY = "Request clarification"
BUDGET_EXCEPTION = "Escalate: budget exception"
MANUAL_REVIEW = "Manual review: evidence unavailable or conflicting"
DO_NOT_PROCEED = "Do not proceed"
RECOMMENDATION_LABELS = [PROCEED, USE_EXISTING, SPECIALIST, CLARIFY, BUDGET_EXCEPTION, MANUAL_REVIEW, DO_NOT_PROCEED]

MISSING_REQUESTER = "Requester and department (requester ID not found)"
MISSING_PRODUCT = "Product and vendor name"
MISSING_COST = "Annual cost or reasonable annual estimate"
MISSING_USERS = "Number of users/licenses"
MISSING_PURPOSE = "Business purpose"
MISSING_DATA_LEVEL = "Intended data-access level"
MISSING_INTEGRATIONS = "Required integrations (state 'none' if there are none)"

MIN_JUSTIFICATION_CHARS = 15

# Threshold table (section 4): (upper bound inclusive, tier label, approvals)
THRESHOLDS: list[tuple[float, str, list[str]]] = [
    (1_000.00, "Up to $1,000", ["Manager"]),
    (10_000.00, "$1,000.01 - $10,000", ["Department Head", "Procurement"]),
    (25_000.00, "$10,000.01 - $25,000", ["Department Head", "Finance", "Procurement"]),
    (float("inf"), "Above $25,000", ["Department Head", "Finance", "CFO", "Procurement"]),
]
LEGAL_NEW_VENDOR_THRESHOLD = 10_000.00  # section 7 uses ">= $10,000" (section 4 Finance uses "> $10,000")

UNKNOWN_VALUES = {"", "unknown", "tbd", "tbc", "n/a", "na", "null", "not_sure", "unsure", "unspecified", "?"}

APPROVED_STATUSES = {"approved", "current", "complete", "completed", "passed", "standard"}
EXPIRED_STATUSES = {"expired", "lapsed", "stale"}
INCOMPLETE_STATUSES = {"pending", "not_completed", "in_progress", "not_started", "new", "draft", "incomplete", "requested"}
REJECTED_STATUSES = {"rejected", "failed", "denied", "blocked", "suspended"}

SECURITY_INTEGRATION_PATTERNS: list[tuple[str, str]] = [
    (r"\bprod(uction)?\b", "production system integration"),
    (r"\bcloud[ -]?accounts?\b|\baws\b|\bgcp\b|\bgoogle cloud\b|\bazure\b|\bkubernetes\b|\bk8s\b", "cloud-account integration"),
    (r"\bgit\b|\bgithub\b|\bgitlab\b|\bbitbucket\b|\bsource[ -]?code\b|\bcode repositor", "source-code repository access"),
    (r"\bvault\b|\bsecrets?\b|\bcredentials?\b|\bpasswords?\b", "credentials/secrets access"),
]
RESTRICTION_PATTERN = re.compile(r"limited|restrict|only for permitted|sensitive[- ]data use requires", re.IGNORECASE)
ONE_TIME_PATTERN = re.compile(r"\bone[- ]time\b|\bone[- ]off\b", re.IGNORECASE)


# ---------------------------------------------------------------- models

class Finding(BaseModel):
    source: str = Field(description="Tool whose data supports the finding")
    finding: str
    reference: str | None = None
    rule: str = Field(description="Engine rule that produced it")


class PolicyInputs(BaseModel):
    request: dict[str, Any]
    approvers: dict[str, Any]
    budget: dict[str, Any] | None
    catalog: list[dict[str, Any]]
    purchase_history: list[dict[str, Any]]
    vendor_record: dict[str, Any] | None
    vendor_risk: dict[str, Any]
    reference_date: date


class PolicyResult(BaseModel):
    request_id: str
    reference_date: date
    annual_cost_usd: float | None
    threshold_tier: str | None
    required_approvals: list[str]
    approval_reasons: dict[str, list[str]]
    approver_assignments: dict[str, str]
    risk_flags: list[str]
    missing_information: list[str]
    findings: list[Finding]
    budget_check: dict[str, Any]
    vendor_assessment: dict[str, Any]
    overlap_candidates: list[dict[str, Any]]
    blocking_issues: list[str]
    untrusted_text_findings: list[UntrustedTextFinding] = Field(default_factory=list)
    baseline_recommendation: str
    baseline_next_step: str
    human_review_required: bool = True


class _Acc:
    """Accumulates approvals, flags, missing info and findings in stable order."""

    def __init__(self) -> None:
        self.approvals: dict[str, list[str]] = {}
        self.flags: list[str] = []
        self.missing: list[str] = []
        self.findings: list[Finding] = []
        self.blocking: list[str] = []

    def approve(self, role: str, reason: str) -> None:
        reasons = self.approvals.setdefault(role, [])
        if reason not in reasons:
            reasons.append(reason)

    def flag(self, name: str) -> None:
        if name not in self.flags:
            self.flags.append(name)

    def need(self, item: str) -> None:
        if item not in self.missing:
            self.missing.append(item)

    def find(self, source: str, text: str, reference: str | None, rule: str) -> None:
        self.findings.append(Finding(source=source, finding=text, reference=reference, rule=rule))

    def block(self, issue: str) -> None:
        if issue not in self.blocking:
            self.blocking.append(issue)


# ---------------------------------------------------------------- helpers

def normalize_token(value: object) -> str:
    return re.sub(r"[\s\-/]+", "_", str(value or "").strip().lower())


def status_category(value: object) -> str:
    token = normalize_token(value)
    if token in APPROVED_STATUSES:
        return "approved"
    if token in EXPIRED_STATUSES:
        return "expired"
    if token in INCOMPLETE_STATUSES:
        return "incomplete"
    if token in REJECTED_STATUSES:
        return "rejected"
    return "unknown"


def parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def money(value: float) -> str:
    return f"${value:,.2f}".replace(".00", "")


def is_unknown(value: object) -> bool:
    return value is None or normalize_token(value) in UNKNOWN_VALUES


def data_profile(level: object) -> dict[str, bool]:
    token = normalize_token(level)
    unknown = is_unknown(level)
    pii = "pii" in token or "personal" in token
    security = (not unknown) and (
        pii
        or token.startswith("production")
        or any(k in token for k in ("source_code", "confidential", "credential", "secret", "restricted"))
    )
    sensitive = (not unknown) and (pii or any(k in token for k in ("confidential", "source_code", "credential", "secret", "restricted")))
    return {"unknown": unknown, "pii": pii, "security": security, "sensitive": sensitive}


def _words(name: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(name or "").lower()))


def _contains_phrase(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(^| ){re.escape(needle)}( |$)", haystack) is not None


# ---------------------------------------------------------------- rules

def _check_required_info(req: dict, approvers: dict, acc: _Acc) -> tuple[float | None, float | None]:
    if approvers.get("requester") is None:
        acc.need(MISSING_REQUESTER)
    if not str(req.get("product_name") or "").strip() or not str(req.get("vendor_name") or "").strip():
        acc.need(MISSING_PRODUCT)

    cost = to_number(req.get("annual_cost_usd"))
    if cost is None or cost < 0:
        acc.need(MISSING_COST)
        cost = None
    users = to_number(req.get("user_count"))
    if users is None or users <= 0:
        acc.need(MISSING_USERS)
        users = None
    if len(str(req.get("business_justification") or "").strip()) < MIN_JUSTIFICATION_CHARS:
        acc.need(MISSING_PURPOSE)
    if is_unknown(req.get("data_access_level")):
        acc.need(MISSING_DATA_LEVEL)
    if req.get("requested_integrations") is None:
        acc.need(MISSING_INTEGRATIONS)

    if acc.missing:
        acc.flag("missing_information")
        acc.block("Required request information is missing")
        acc.find(SRC_REQUEST, "Request is missing required information: " + "; ".join(acc.missing),
                 "Policy section 1", "required_information")
    else:
        acc.find(SRC_REQUEST, "All section 1 required request fields are present", "Policy section 1", "required_information")

    if ONE_TIME_PATTERN.search(str(req.get("business_justification") or "")) and cost is not None:
        acc.find(SRC_REQUEST, f"Cost is described as one-time; {money(cost)} is treated as the annualized amount for thresholds",
                 "Policy section 4", "annualization")
    urgency = normalize_token(req.get("urgency"))
    if urgency in {"high", "urgent", "critical", "asap"}:
        acc.find(SRC_REQUEST, f"Urgency is '{req.get('urgency')}'; urgency does not waive any review or approval",
                 "Policy section 11", "urgency")
    return cost, users


def _assign_approvers(approvers: dict, acc: _Acc) -> None:
    requester = approvers.get("requester")
    if requester:
        acc.find(SRC_REQUEST, f"Requester {requester['name']} ({requester['employee_id']}), {requester['department']}, level {requester['level']}",
                 requester["employee_id"], "requester")
    for note in approvers.get("notes", []):
        acc.find(SRC_REQUEST, note, "employees.csv", "approver_resolution")


def _check_threshold(cost: float | None, acc: _Acc) -> str | None:
    if cost is None:
        acc.approve("Procurement", "Annual cost unknown: approval tier cannot be determined; Procurement to triage (section 4)")
        acc.find(SRC_POLICY, "Approval threshold cannot be computed without an annual cost", "Policy section 4", "threshold")
        return None
    amount = round(cost, 2)
    for upper, label, roles in THRESHOLDS:
        if amount <= upper:
            for role in roles:
                acc.approve(role, f"Annual amount {money(amount)} is in tier '{label}' (section 4)")
            acc.find(SRC_POLICY, f"Annual amount {money(amount)} falls in tier '{label}': minimum approvals {', '.join(roles)}",
                     "Policy section 4", "threshold")
            return label
    return None  # unreachable


def _check_budget(cost: float | None, approvers: dict, budget: dict | None, acc: _Acc) -> dict:
    department = (approvers.get("requester") or {}).get("department")
    result: dict[str, Any] = {"department": department, "status": "not_checked"}
    if department is None:
        return result
    if budget is None or budget.get("available_usd") is None:
        result["status"] = "budget_record_missing"
        acc.flag("budget_record_missing")
        acc.approve("Finance", f"No software budget record for department '{department}'; budget cannot be verified (section 2)")
        acc.find(SRC_BUDGET, f"No software budget record found for department '{department}'", "department_budgets.csv", "budget")
        return result
    available = budget["available_usd"]
    result.update({k: budget.get(k) for k in ("annual_software_budget_usd", "committed_usd", "available_usd")})
    if cost is None:
        result["status"] = "cost_unknown"
        acc.find(SRC_BUDGET, f"{department} has {money(available)} available; budget fit cannot be checked without a cost",
                 f"department_budgets.csv:{department}", "budget")
        return result
    remaining = round(available - cost, 2)
    result["remaining_after_usd"] = remaining
    if cost <= available:
        result["status"] = "within_budget"
        acc.find(SRC_BUDGET, f"Cost {money(cost)} is within {department}'s available budget of {money(available)} "
                             f"({money(remaining)} would remain). A positive budget check does not imply approval.",
                 f"department_budgets.csv:{department}", "budget")
    else:
        result["status"] = "insufficient"
        result["shortfall_usd"] = round(cost - available, 2)
        acc.flag("budget_insufficient")
        acc.approve("Finance", f"Cost exceeds available budget by {money(cost - available)}: budget exception review (section 2)")
        acc.block("Budget insufficient")
        acc.find(SRC_BUDGET, f"Cost {money(cost)} exceeds {department}'s available budget of {money(available)} "
                             f"by {money(cost - available)}", f"department_budgets.csv:{department}", "budget")
    return result


def _check_security_triggers(req: dict, profile: dict, acc: _Acc) -> None:
    level = req.get("data_access_level")
    if profile["security"]:
        acc.approve("Security", f"Data-access level '{level}' requires Security review (section 5)")
        acc.find(SRC_REQUEST, f"Data-access level '{level}' is a section 5 Security trigger", "Policy section 5", "security_data")
    elif profile["unknown"]:
        acc.approve("Security", "Data-access level unknown: sensitive access cannot be ruled out (sections 5, 10)")
    for integration in req.get("requested_integrations") or []:
        for pattern, label in SECURITY_INTEGRATION_PATTERNS:
            if re.search(pattern, str(integration), re.IGNORECASE):
                acc.approve("Security", f"Integration '{integration}' is a {label} (section 5)")
                acc.find(SRC_REQUEST, f"Requested integration '{integration}' is a {label}, a section 5 Security trigger",
                         "Policy section 5", "security_integration")
                break


def _source_state(label: str, status: object, review_date: object, ref: date) -> dict:
    parsed = parse_date(review_date)
    days = (ref - parsed).days if parsed else None
    category = status_category(status)
    return {
        "source": label,
        "status_raw": status,
        "status": category,
        "review_date": parsed.isoformat() if parsed else None,
        "days_since_review": days,
        "date_expired": days is not None and days > SECURITY_REVIEW_VALIDITY_DAYS,
        "date_in_future": days is not None and days < 0,
    }


def _check_vendor(req: dict, inputs: PolicyInputs, cost: float | None, profile: dict, acc: _Acc) -> dict:
    vendor = str(req.get("vendor_name") or "").strip()
    ref = inputs.reference_date
    record = inputs.vendor_record
    risk = inputs.vendor_risk or {}
    states: list[dict] = []
    assessment: dict[str, Any] = {"vendor_name": vendor, "registry": None, "api": None, "api_status": risk.get("status")}

    # --- internal registry
    if record is None:
        is_new_vendor, terms_category = True, "unknown"
        acc.flag("vendor_not_in_registry")
        acc.approve("Procurement", "Vendor is not in the internal registry: onboarding required")
        acc.find(SRC_REGISTRY, f"Vendor '{vendor}' has no record in the internal vendor registry", "vendors.csv", "registry")
    else:
        procurement_category = status_category(record.get("procurement_status"))
        is_new_vendor = procurement_category != "approved"
        terms_category = status_category(record.get("legal_terms_status"))
        state = _source_state("registry", record.get("security_status"), record.get("security_review_date"), ref)
        states.append(state)
        assessment["registry"] = state | {
            "procurement_status": record.get("procurement_status"),
            "legal_terms_status": record.get("legal_terms_status"),
            "notes": record.get("notes"),
        }
        acc.find(SRC_REGISTRY,
                 f"Registry {record.get('vendor_id')}: procurement '{record.get('procurement_status')}', security "
                 f"'{record.get('security_status')}' (review date {record.get('security_review_date') or 'none'}), "
                 f"legal terms '{record.get('legal_terms_status')}'. Notes: {record.get('notes') or '-'}",
                 record.get("vendor_id"), "registry")
        if procurement_category == "rejected":
            acc.flag("vendor_blocked")
            acc.block(f"Vendor procurement status is '{record.get('procurement_status')}'")
    assessment["is_new_vendor"] = is_new_vendor

    # --- external vendor-risk service
    endpoint = f"GET /vendor-risk/{vendor}"
    api_status = risk.get("status")
    data = risk.get("data") or {}
    if api_status == "ok":
        state = _source_state("vendor_risk_api", data.get("security_review_status"), data.get("last_review_date"), ref)
        states.append(state)
        assessment["api"] = state | {k: data.get(k) for k in ("risk_level", "processes_personal_data", "stores_data_outside_region", "notes")}
        acc.find(SRC_VENDOR_RISK,
                 f"Vendor-risk API: risk '{data.get('risk_level')}', security review '{data.get('security_review_status')}' "
                 f"(last review {data.get('last_review_date') or 'none'}), processes personal data: {data.get('processes_personal_data')}, "
                 f"stores data outside region: {data.get('stores_data_outside_region')}. Notes: {data.get('notes') or '-'}",
                 endpoint, "vendor_risk")
        if normalize_token(data.get("risk_level")) in {"high", "critical"}:
            acc.flag("high_vendor_risk")
            acc.approve("Security", f"Vendor-risk API rates the vendor '{data.get('risk_level')}' risk (section 5)")
    elif api_status == "not_found":
        acc.flag("vendor_risk_record_missing")
        acc.approve("Security", "Vendor-risk service has no record for this vendor; assessment cannot be verified (sections 5, 10)")
        acc.block("Vendor-risk record missing")
        acc.find(SRC_VENDOR_RISK, f"Vendor-risk service has no record for '{vendor}'; no favourable status inferred",
                 endpoint, "vendor_risk")
    else:
        acc.flag("vendor_risk_unavailable")
        acc.approve("Security", "Vendor-risk service unavailable; vendor security status could not be verified (section 10)")
        acc.block("Vendor-risk evidence unavailable")
        acc.find(SRC_VENDOR_RISK,
                 f"Vendor-risk service unavailable for '{vendor}' ({risk.get('error_detail') or api_status}); "
                 "security status, personal-data processing and data residency could not be verified. No favourable status inferred.",
                 endpoint, "vendor_risk")

    # --- assessment currency (section 5: current for 365 days)
    expired = [s for s in states if s["status"] == "expired" or s["date_expired"]]
    incomplete = [s for s in states if s["status"] in {"incomplete", "unknown"} or (s["status"] == "approved" and not s["review_date"])]
    rejected = [s for s in states if s["status"] == "rejected"]
    future = [s for s in states if s["date_in_future"]]
    for s in expired:
        acc.flag("vendor_review_expired")
        detail = f"{s['days_since_review']} days old" if s["days_since_review"] is not None else "marked expired"
        acc.approve("Security", f"Vendor security assessment expired per {s['source']} ({detail}; valid {SECURITY_REVIEW_VALIDITY_DAYS} days) (section 5)")
        acc.find(SRC_REGISTRY if s["source"] == "registry" else SRC_VENDOR_RISK,
                 f"Security assessment per {s['source']} is expired: status '{s['status_raw']}', review date {s['review_date'] or 'none'}"
                 + (f" ({s['days_since_review']} days before the {ref.isoformat()} reference date)" if s["days_since_review"] is not None else ""),
                 "Policy section 5", "assessment_expiry")
    for s in incomplete:
        acc.approve("Security", f"Vendor security assessment missing or not completed per {s['source']} (status '{s['status_raw'] or 'none'}') (section 5)")
    for s in rejected:
        acc.flag("vendor_security_rejected")
        acc.approve("Security", f"Vendor security assessment status '{s['status_raw']}' per {s['source']} (section 5)")
        acc.block(f"Vendor security assessment '{s['status_raw']}'")
    for s in future:
        acc.approve("Security", f"Review date {s['review_date']} per {s['source']} is after the reference date; record cannot be trusted")
        acc.find(SRC_POLICY, f"{s['source']} review date {s['review_date']} is after the {ref.isoformat()} reference date", "Policy section 5", "assessment_expiry")
    if not states:
        acc.approve("Security", "No vendor security assessment evidence available from any source (section 5)")

    # --- registry vs external service (section 5: surface conflicts, never pick one)
    conflicts: list[str] = []
    registry_state = next((s for s in states if s["source"] == "registry"), None)
    api_state = next((s for s in states if s["source"] == "vendor_risk_api"), None)
    if registry_state and api_state:
        if "unknown" not in {registry_state["status"], api_state["status"]} and registry_state["status"] != api_state["status"]:
            conflicts.append(f"registry security status '{registry_state['status_raw']}' vs vendor-risk API '{api_state['status_raw']}'")
        if registry_state["review_date"] and api_state["review_date"] and registry_state["review_date"] != api_state["review_date"]:
            conflicts.append(f"registry review date {registry_state['review_date']} vs vendor-risk API {api_state['review_date']}")
    if conflicts:
        acc.flag("conflicting_vendor_evidence")
        acc.approve("Security", "Internal registry and vendor-risk service disagree; manual Security review (section 5)")
        acc.block("Conflicting vendor evidence")
        acc.find(SRC_POLICY, "Conflicting vendor evidence: " + "; ".join(conflicts) + ". Neither source is assumed correct.",
                 "Policy section 5", "vendor_conflict")
    assessment["conflicts"] = conflicts
    assessment["assessment_current"] = bool(states) and not (expired or incomplete or rejected or future or conflicts) and api_status == "ok"

    # --- privacy (section 6) and legal (section 7)
    api_ok = api_status == "ok"
    outside_region = api_ok and data.get("stores_data_outside_region") is True
    level = req.get("data_access_level")
    if profile["pii"]:
        acc.approve("Privacy", f"Tool will process '{level}' (section 6)")
    if outside_region and profile["sensitive"]:
        acc.approve("Privacy", f"Vendor stores data outside the operating region and the request involves '{level}' (section 6)")
        acc.approve("Legal", "Cross-region storage of sensitive data is a material data-processing issue (section 7)")
        acc.find(SRC_VENDOR_RISK, f"Vendor stores data outside the operating region while the request involves '{level}'",
                 endpoint, "data_residency")
    elif not api_ok and profile["sensitive"]:
        acc.approve("Privacy", f"Data residency could not be verified (vendor-risk service unavailable) for '{level}' (sections 6, 10)")
    if api_ok and data.get("processes_personal_data") is True and not profile["pii"] and not profile["unknown"]:
        acc.find(SRC_VENDOR_RISK, f"Vendor processes personal data, although the request declares '{level}'; confirm no personal data is in scope",
                 endpoint, "personal_data_note")

    if is_new_vendor:
        if cost is None:
            acc.approve("Legal", "New vendor and annual cost unknown: the $10,000 Legal threshold cannot be ruled out (section 7)")
        elif cost >= LEGAL_NEW_VENDOR_THRESHOLD:
            acc.approve("Legal", f"New vendor with annual spend {money(cost)} (>= $10,000) (section 7)")
    if terms_category != "approved":
        terms = record.get("legal_terms_status") if record else "unknown (vendor not in registry)"
        acc.approve("Legal", f"Vendor legal terms are '{terms or 'unknown'}', not approved/standard (section 7)")
    return assessment


COMPANY_WIDE_SCOPES = {"company wide", "company", "all", "global"}


def find_overlap_candidates(request: dict, requester_department: str | None,
                            catalog: list[dict], purchase_history: list[dict]) -> list[dict]:
    """Catalog products matching the request by product, product family, vendor or category (section 3)."""
    requested_product = _words(request.get("product_name"))
    requested_vendor = _words(request.get("vendor_name"))
    requested_category = _words(request.get("category"))
    department = _words(requester_department)
    candidates: list[dict] = []
    for row in catalog:
        product, vendor, category = _words(row.get("product_name")), _words(row.get("vendor_name")), _words(row.get("category"))
        reasons = []
        if requested_product and product == requested_product:
            reasons.append("same product")
        elif _contains_phrase(requested_product, product) or _contains_phrase(product, requested_product):
            reasons.append("same product family")
        if requested_vendor and vendor == requested_vendor:
            reasons.append("same vendor")
        if requested_category and category == requested_category:
            reasons.append("same category")
        if not reasons:
            continue
        scope = str(row.get("scope") or "")
        available = _words(scope) in COMPANY_WIDE_SCOPES or bool(department and _words(scope) == department)
        candidates.append({
            "software_id": row.get("software_id"),
            "product_name": row.get("product_name"),
            "vendor_name": row.get("vendor_name"),
            "category": row.get("category"),
            "status": row.get("status"),
            "annual_cost_usd": to_number(row.get("annual_cost_usd")),
            "licensed_seats": to_number(row.get("licensed_seats")),
            "scope": scope,
            "notes": row.get("notes"),
            "match_reasons": reasons,
            "relationship": "same product / expansion" if {"same product", "same product family", "same vendor"} & set(reasons) else "alternative",
            "available_to_requester": available,
            "purchase_history": [
                {k: h.get(k) for k in ("purchase_id", "purchase_date", "department", "annual_amount_usd", "status", "notes")}
                for h in purchase_history
                if _words(h.get("product_name")) == product or _words(h.get("vendor_name")) == vendor
            ],
        })
    return candidates


def describe_candidate(c: dict) -> str:
    seats = c["licensed_seats"]
    return (f"Catalog {c['software_id']} {c['product_name']} ({c['category']}, {c['status']}, "
            f"{int(seats) if seats is not None else '?'} seats, scope {c['scope'] or '?'}): {', '.join(c['match_reasons'])}; "
            f"{'available to' if c['available_to_requester'] else 'not licensed for'} the requester's department. "
            f"Notes: {c['notes'] or '-'}")


def _find_overlap(req: dict, inputs: PolicyInputs, acc: _Acc) -> list[dict]:
    department = (inputs.approvers.get("requester") or {}).get("department")
    candidates = find_overlap_candidates(req, department, inputs.catalog, inputs.purchase_history)
    for c in candidates:
        acc.find(SRC_CATALOG, describe_candidate(c), c["software_id"], "overlap")
    if candidates:
        acc.flag("existing_tool_overlap")
    else:
        acc.find(SRC_CATALOG, "No approved catalog product with the same product, vendor or category", "software_catalog.csv", "overlap")
    return candidates


def _check_restricted_use(req: dict, inputs: PolicyInputs, candidates: list[dict], profile: dict, acc: _Acc) -> None:
    """Section 8: an approval for limited use does not cover sensitive data classes."""
    vendor = _words(req.get("vendor_name"))
    restrictions: list[tuple[str, str, str]] = []
    for c in candidates:
        if _words(c["vendor_name"]) == vendor and RESTRICTION_PATTERN.search(f"{c['status']} {c['notes']}"):
            restrictions.append((SRC_CATALOG, f"catalog {c['software_id']} is '{c['status']}' ({c['notes']})", c["software_id"]))
    record = inputs.vendor_record or {}
    if RESTRICTION_PATTERN.search(str(record.get("notes") or "")):
        restrictions.append((SRC_REGISTRY, f"registry notes '{record.get('notes')}'", record.get("vendor_id")))
    api_notes = ((inputs.vendor_risk or {}).get("data") or {}).get("notes")
    if RESTRICTION_PATTERN.search(str(api_notes or "")):
        restrictions.append((SRC_VENDOR_RISK, f"vendor-risk notes '{api_notes}'", f"GET /vendor-risk/{req.get('vendor_name')}"))
    if not restrictions:
        return
    level = req.get("data_access_level")
    summary = "; ".join(r[1] for r in restrictions)
    if profile["sensitive"]:
        acc.flag("ai_use_case_not_covered")
        acc.approve("Security", f"Existing approval is restricted ({summary}) and does not cover '{level}' (section 8)")
        if profile["pii"]:
            acc.approve("Privacy", f"Restricted approval does not cover personal data '{level}' (section 8)")
        acc.find(restrictions[0][0], f"Existing approval is restricted ({summary}); it does not authorise '{level}'",
                 "Policy section 8", "restricted_use")
    elif profile["unknown"]:
        acc.find(restrictions[0][0], f"Existing approval is restricted ({summary}); the data class must be confirmed before use",
                 "Policy section 8", "restricted_use")


def _scan_untrusted(req: dict, inputs: PolicyInputs, candidates: list[dict], acc: _Acc) -> list[UntrustedTextFinding]:
    """Section 9: surface embedded instructions and approval claims; they never change the floor."""
    items: list[tuple[str, str, object]] = [(SRC_REQUEST, f"request.{k}", v) for k, v in req.items()]
    items += [(SRC_REGISTRY, f"vendor_registry.{k}", v) for k, v in (inputs.vendor_record or {}).items()]
    items += [(SRC_VENDOR_RISK, f"vendor_risk.{k}", v) for k, v in ((inputs.vendor_risk or {}).get("data") or {}).items()]
    for c in candidates:
        items += [(SRC_CATALOG, f"catalog.{c['software_id']}.{k}", c.get(k)) for k in ("product_name", "status", "notes")]
        items += [(SRC_CATALOG, f"purchase_history.{h['purchase_id']}.notes", h.get("notes")) for h in c["purchase_history"]]
    findings = scan_fields(items)
    grouped: dict[tuple[str, str, str], list[UntrustedTextFinding]] = {}
    for f in findings:
        grouped.setdefault((f.kind, f.source, f.field), []).append(f)
    for (kind, source, field), group in grouped.items():
        acc.flag(kind)
        labels = ", ".join(f.label for f in group)
        excerpt = max((f.excerpt for f in group), key=len)
        if kind == INJECTION:
            text = f"Embedded instructions in {field} ({labels}) were ignored and treated as data: \"{excerpt}\""
        else:
            text = f"{field} claims an approval (\"{excerpt}\"); claims in business data are not approval evidence and were not relied on"
        acc.find(source, text, "Policy section 9", "untrusted_content")
    return findings


# Most to least restrictive. When several apply, the earliest wins.
RECOMMENDATION_PRECEDENCE = [CLARIFY, DO_NOT_PROCEED, MANUAL_REVIEW, BUDGET_EXCEPTION, USE_EXISTING, SPECIALIST, PROCEED]
VENDOR_EVIDENCE_FLAGS = {"vendor_risk_unavailable", "vendor_risk_record_missing", "conflicting_vendor_evidence"}


def minimum_recommendation(flags: set[str], approvals: list[str], missing: list[str]) -> str:
    """Least restrictive label the deterministic evidence allows (guardrail floor)."""
    if missing:
        return CLARIFY
    if flags & {"vendor_blocked", "vendor_security_rejected"}:
        return DO_NOT_PROCEED
    if flags & (VENDOR_EVIDENCE_FLAGS | {"budget_record_missing"}):
        return MANUAL_REVIEW
    if "budget_insufficient" in flags:
        return BUDGET_EXCEPTION
    if any(r in approvals for r in ("Security", "Privacy", "Legal")):
        return SPECIALIST
    return PROCEED


def next_step_for(label: str, approvals: list[str], missing: list[str], flags: set[str]) -> str:
    approver_text = ", ".join(approvals) if approvals else "the appropriate approvers"
    specialists = [r for r in ("Security", "Privacy", "Legal") if r in approvals]
    if label == CLARIFY:
        items = "; ".join(missing) if missing else "the information needed to assess the request"
        return f"Ask the requester for: {items}. Do not route for approval until the request is complete."
    if label == DO_NOT_PROCEED:
        return "Do not proceed with this vendor; Procurement and Security to confirm the vendor's status with the requester."
    if label == MANUAL_REVIEW:
        lead, task = ("Security", "verify the vendor manually") if flags & VENDOR_EVIDENCE_FLAGS or "budget_record_missing" not in flags             else ("Finance", "verify the department budget")
        others = ", ".join(r for r in approvals if r != lead) or "the required approvers"
        return (f"Send to {lead} to {task} before any approval; then route to {others}. "
                "Record which evidence could not be verified.")
    if label == BUDGET_EXCEPTION:
        return f"Send to Finance for a budget exception decision, with the evidence pack for {approver_text}."
    if label == SPECIALIST:
        return f"Send the evidence pack to {', '.join(specialists) or 'the specialist reviewers'} for review, then route to {approver_text}."
    if label == USE_EXISTING:
        return "Ask the requester to confirm why the existing approved tool cannot meet the need before a new purchase."
    return f"Route to {approver_text} for approval."


def _baseline(acc: _Acc, approvals: list[str], candidates: list[dict], missing: list[str]) -> tuple[str, str]:
    """Deterministic reference recommendation, used as the no-LLM fallback and eval baseline."""
    flags = set(acc.flags)
    label = minimum_recommendation(flags, approvals, missing)
    if label == PROCEED and any(c["relationship"] == "alternative" and c["available_to_requester"] for c in candidates):
        label = USE_EXISTING
    return label, next_step_for(label, approvals, missing, flags)


# ---------------------------------------------------------------- entry point

def evaluate(inputs: PolicyInputs) -> PolicyResult:
    req = inputs.request
    acc = _Acc()
    profile = data_profile(req.get("data_access_level"))

    cost, _users = _check_required_info(req, inputs.approvers, acc)
    _assign_approvers(inputs.approvers, acc)
    tier = _check_threshold(cost, acc)
    budget_check = _check_budget(cost, inputs.approvers, inputs.budget, acc)
    _check_security_triggers(req, profile, acc)
    vendor_assessment = _check_vendor(req, inputs, cost, profile, acc)
    candidates = _find_overlap(req, inputs, acc)
    _check_restricted_use(req, inputs, candidates, profile, acc)
    untrusted = _scan_untrusted(req, inputs, candidates, acc)

    if "Security" in acc.approvals:
        acc.flag("security_review_required")
    if "Privacy" in acc.approvals:
        acc.flag("privacy_review_required")
    if "Legal" in acc.approvals:
        acc.flag("legal_review_required")

    approvals = [role for role in APPROVAL_ORDER if role in acc.approvals]
    reasons = {role: acc.approvals[role] for role in approvals}
    assignments: dict[str, str] = {}
    for role, key in (("Manager", "manager"), ("Department Head", "department_head")):
        if role in approvals:
            person = inputs.approvers.get(key)
            assignments[role] = f"{person['name']} ({person['employee_id']})" if person else "To be assigned by Procurement"

    label, next_step = _baseline(acc, approvals, candidates, acc.missing)
    if untrusted:
        next_step += (" Reviewer note: the request data contains embedded instructions or approval claims; "
                      "they were ignored and every approval above is still required.")
    return PolicyResult(
        request_id=str(req.get("request_id") or ""),
        reference_date=inputs.reference_date,
        annual_cost_usd=cost,
        threshold_tier=tier,
        required_approvals=approvals,
        approval_reasons=reasons,
        approver_assignments=assignments,
        risk_flags=acc.flags,
        missing_information=acc.missing,
        findings=acc.findings,
        budget_check=budget_check,
        vendor_assessment=vendor_assessment,
        overlap_candidates=candidates,
        blocking_issues=acc.blocking,
        untrusted_text_findings=untrusted,
        baseline_recommendation=label,
        baseline_next_step=next_step,
    )
