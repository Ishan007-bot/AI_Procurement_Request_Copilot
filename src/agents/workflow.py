"""Workflow + 1 LLM call: code gathers the evidence in a fixed order, one model call decides.

The evaluation showed the agents always call the same evidence tools, so this tests
whether letting a model choose the tools adds anything. The same evidence pack and
Reviewer prompt as architecture B are used; only the Analyst agent is replaced by code.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.loop import LoopResult, run_tool_loop
from src.agents.single_agent import parse_draft
from src.agents.staged import build_evidence_pack
from src.contracts import AnalystReport, DraftDecision, DraftEvidence, EvidencePack
from src.llm import ChatClient
from src.prompts import REVIEWER_SYSTEM, reviewer_task, submit_decision_schema
from src.tools import ToolRunner, wrap_for_model

EVIDENCE_TOOLS = ["get_request_context", "check_budget", "search_software_catalog",
                  "get_vendor_registry_record", "get_vendor_risk", "evaluate_policy_rules"]


@dataclass
class WorkflowResult:
    output: DraftDecision
    reviewer: LoopResult[DraftDecision]
    pack: EvidencePack


def deterministic_report(runner: ToolRunner) -> AnalystReport:
    """The analyst's report, built by code from the tool results (no model)."""
    policy = runner.policy_result()
    request = runner.request
    return AnalystReport(
        request_summary=(f"{request.get('requester_id')} requests {request.get('product_name')} from "
                         f"{request.get('vendor_name')} (evidence gathered by a fixed workflow)."),
        key_findings=[DraftEvidence(source=f.source, finding=f.finding, reference=f.reference) for f in policy.findings],
        existing_tool_assessment="",  # left to the reviewer: this is the judgement the model is for
        data_quality_issues=list(policy.blocking_issues),
        untrusted_content_notes="; ".join(f"{f.kind} in {f.field}" for f in policy.untrusted_text_findings),
        open_questions=[],
    )


def run_workflow(request: dict, runner: ToolRunner, client: ChatClient) -> WorkflowResult:
    for name in EVIDENCE_TOOLS:
        runner.call(name, {}, call_id=f"workflow_{name}")
    pack = build_evidence_pack(request, deterministic_report(runner), runner)
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
    return WorkflowResult(output=reviewer.output, reviewer=reviewer, pack=pack)
