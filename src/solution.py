"""Assessment adapter and product entry point.

`handle_request(request_id, architecture)` is the evaluation contract.
`run_copilot(...)` returns the decision plus everything the UI and evaluation
need (policy floor, tool log, draft, telemetry).

Flow for every architecture:
  agent(s) -> draft  ->  guardrails (policy floor, grounding, consistency)  ->  decision
If no model is usable or the agent fails, a conservative rules-only decision is
returned instead of an error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.agents.loop import AgentLoopError
from src.agents.single_agent import run_single_agent
from src.agents.staged import run_staged
from src.contracts import Architecture, DraftDecision, ProcurementDecision
from src.data_access import get_request
from src.guardrails import apply_guardrails, fallback_decision
from src.llm import ChatClient, LLMError, LLMUnavailable, get_llm_client
from src.policy_engine import PolicyResult
from src.telemetry import RunTelemetryCounter
from src.tools import ToolResult, ToolRunner

ClientFactory = Callable[[RunTelemetryCounter], ChatClient]


@dataclass
class CopilotRun:
    architecture: str
    request: dict
    decision: ProcurementDecision
    policy: PolicyResult
    tool_results: list[ToolResult]
    telemetry: dict
    draft: DraftDecision | None = None
    stages: dict = field(default_factory=dict)
    fallback_reason: str | None = None
    model: str | None = None


def run_copilot(request: dict, architecture: Architecture = "single",
                client_factory: ClientFactory | None = None,
                on_tool_call: Callable[[ToolResult], None] | None = None) -> CopilotRun:
    telemetry = RunTelemetryCounter()
    runner = ToolRunner(request, telemetry, listener=on_tool_call)
    draft: DraftDecision | None = None
    stages: dict = {}
    fallback_reason: str | None = None
    model: str | None = None

    try:
        client = (client_factory or get_llm_client)(telemetry)
        model = f"{client.provider}:{client.model}"
        if architecture == "single":
            result = run_single_agent(request, runner, client)
            draft = result.output
            stages = {"agent": {"iterations": result.iterations, "repairs": result.repairs,
                                "recovered_from_text": result.recovered_from_text}}
        elif architecture == "staged":
            result = run_staged(request, runner, client)
            draft = result.output
            stages = {
                "analyst": {"iterations": result.analyst.iterations, "repairs": result.analyst.repairs,
                            "llm_calls": result.analyst_llm_calls, "recovered_from_text": result.analyst.recovered_from_text},
                "reviewer": {"iterations": result.reviewer.iterations, "repairs": result.reviewer.repairs,
                             "llm_calls": result.reviewer_llm_calls, "recovered_from_text": result.reviewer.recovered_from_text},
                "evidence_pack": result.pack.model_dump(),
            }
        else:
            raise ValueError(f"Unknown architecture '{architecture}'")
    except (LLMUnavailable, LLMError, AgentLoopError) as exc:
        fallback_reason = f"{type(exc).__name__}: {exc}"
        telemetry.record_error(fallback_reason)

    policy = runner.policy_result()
    if draft is None:
        decision = fallback_decision(policy, telemetry, fallback_reason or "no draft")
    else:
        decision = apply_guardrails(draft, policy, request, runner.tools_called, telemetry)
    decision.telemetry = telemetry.to_contract()
    return CopilotRun(architecture=architecture, request=request, decision=decision, policy=policy,
                      tool_results=list(runner.results), telemetry=telemetry.summary(), draft=draft,
                      stages=stages, fallback_reason=fallback_reason, model=model)


def handle_request_payload(request: dict, architecture: Architecture = "single",
                           client_factory: ClientFactory | None = None) -> ProcurementDecision:
    return run_copilot(request, architecture, client_factory).decision


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter used by the public and hidden evaluation harness."""
    return handle_request_payload(get_request(request_id), architecture)
