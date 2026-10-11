"""Architecture B: Analyst agent (tools) -> structured evidence pack -> Reviewer agent (no tools).

The hand-off is explicit and typed (`EvidencePack`): the analyst's report plus the
deterministic results the tools produced (policy floor, budget, vendor assessment,
overlap candidates). The reviewer never sees raw conversation history, only the pack.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.loop import LoopResult, run_tool_loop
from src.agents.single_agent import parse_draft, request_intake
from src.contracts import AnalystReport, DraftDecision, EvidencePack
from src.llm import ChatClient
from src.prompts import (
    ANALYST_SYSTEM, REVIEWER_SYSTEM, analyst_task, reviewer_task, submit_decision_schema,
    submit_evidence_pack_schema,
)
from src.tools import ToolRunner, wrap_for_model


@dataclass
class StagedResult:
    output: DraftDecision
    analyst: LoopResult[AnalystReport]
    reviewer: LoopResult[DraftDecision]
    pack: EvidencePack
    analyst_llm_calls: int
    reviewer_llm_calls: int


def parse_report(arguments: dict) -> AnalystReport:
    report = AnalystReport.model_validate(arguments)
    if not report.request_summary.strip():
        raise ValueError("request_summary must not be empty")
    return report


def build_evidence_pack(request: dict, report: AnalystReport, runner: ToolRunner) -> EvidencePack:
    """Deterministic part of the hand-off: taken from tool results, not from the analyst's prose."""
    policy = runner.policy_result()
    return EvidencePack(
        request=request,
        analyst=report,
        policy_floor={
            "required_approvals": policy.required_approvals,
            "approval_reasons": {role: reasons[0] for role, reasons in policy.approval_reasons.items() if reasons},
            "approver_assignments": policy.approver_assignments,
            "risk_flags": policy.risk_flags,
            "missing_information": policy.missing_information,
            "blocking_issues": policy.blocking_issues,
            "threshold_tier": policy.threshold_tier,
            "reference_date": policy.reference_date.isoformat(),
        },
        budget=policy.budget_check,
        vendor_assessment={k: policy.vendor_assessment.get(k)
                           for k in ("vendor_name", "is_new_vendor", "assessment_current", "conflicts", "api_status", "registry", "api")},
        overlap_candidates=[{k: c.get(k) for k in ("software_id", "product_name", "category", "status", "licensed_seats",
                                                    "scope", "notes", "match_reasons", "relationship", "available_to_requester")}
                            for c in policy.overlap_candidates],
        tools_called=sorted(runner.tools_called),
    )


def run_staged(request: dict, runner: ToolRunner, client: ChatClient) -> StagedResult:
    telemetry = runner.telemetry
    before = telemetry.llm_calls
    analyst = run_tool_loop(
        client=client,
        system_prompt=ANALYST_SYSTEM,
        task=analyst_task(str(request.get("request_id") or ""), request_intake(runner)),
        runner=runner,
        tool_names=None,
        terminal_schema=submit_evidence_pack_schema(),
        parse=parse_report,
    )
    analyst_calls = telemetry.llm_calls - before

    pack = build_evidence_pack(request, analyst.output, runner)
    before = telemetry.llm_calls
    reviewer = run_tool_loop(
        client=client,
        system_prompt=REVIEWER_SYSTEM,
        task=reviewer_task(wrap_for_model(pack.model_dump())),
        runner=None,
        tool_names=[],
        terminal_schema=submit_decision_schema(),
        parse=parse_draft,
        max_iterations=4,
    )
    return StagedResult(output=reviewer.output, analyst=analyst, reviewer=reviewer, pack=pack,
                        analyst_llm_calls=analyst_calls, reviewer_llm_calls=telemetry.llm_calls - before)
