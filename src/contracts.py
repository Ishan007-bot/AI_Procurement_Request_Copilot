from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    source: str = Field(description="Tool/data source name")
    finding: str = Field(description="Concise factual finding")
    reference: str | None = Field(default=None, description="Optional record ID / policy section / endpoint")


class RunTelemetry(BaseModel):
    llm_calls: int | None = None
    tool_calls: int | None = None
    tool_names: list[str] = Field(default_factory=list)


class ProcurementDecision(BaseModel):
    request_id: str
    recommendation: str = Field(description="Short recommendation label or sentence")
    evidence: list[EvidenceItem] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    next_step: str
    human_review_required: bool = True
    telemetry: RunTelemetry | None = None


Architecture = Literal["single", "staged"]

# Suggested approval names for consistency in evaluation:
# Manager, Department Head, Procurement, Finance, CFO, Security, Privacy, Legal
#
# Suggested risk-flag taxonomy (you may add others):
# existing_tool_overlap
# budget_insufficient
# security_review_required
# privacy_review_required
# legal_review_required
# vendor_review_expired
# conflicting_vendor_evidence
# vendor_risk_unavailable
# prompt_injection_detected
# missing_information


# ---------------------------------------------------------------------------
# Internal models (not part of the evaluation contract above).

class DraftEvidence(BaseModel):
    source: str
    finding: str
    reference: str | None = None


class DraftDecision(BaseModel):
    """What an agent submits via the `submit_decision` tool, before guardrails."""
    recommendation: str
    rationale: str = ""
    existing_tool_assessment: str = ""
    evidence: list[DraftEvidence] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    next_step: str = ""


class AnalystReport(BaseModel):
    """What the Analyst agent (architecture B, stage 1) submits via `submit_evidence_pack`."""
    request_summary: str
    key_findings: list[DraftEvidence] = Field(default_factory=list)
    existing_tool_assessment: str = ""
    data_quality_issues: list[str] = Field(default_factory=list)
    untrusted_content_notes: str = ""
    open_questions: list[str] = Field(default_factory=list)


class EvidencePack(BaseModel):
    """Hand-off from Analyst to Reviewer: the analyst's report plus deterministic tool results."""
    request: dict
    analyst: AnalystReport
    policy_floor: dict
    budget: dict | None = None
    vendor_assessment: dict = Field(default_factory=dict)
    overlap_candidates: list[dict] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
