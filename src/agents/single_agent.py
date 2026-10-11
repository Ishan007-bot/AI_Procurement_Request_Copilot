"""Architecture A: one agent gathers evidence with tools and submits the decision."""
from __future__ import annotations

from src.agents.loop import LoopResult, run_tool_loop
from src.contracts import DraftDecision
from src.llm import ChatClient
from src.policy_engine import RECOMMENDATION_LABELS
from src.prompts import SINGLE_AGENT_SYSTEM, single_agent_task, submit_decision_schema
from src.tools import ToolRunner, render_for_model


def parse_draft(arguments: dict) -> DraftDecision:
    draft = DraftDecision.model_validate(arguments)
    if draft.recommendation not in RECOMMENDATION_LABELS:
        raise ValueError(f"recommendation must be one of: {', '.join(RECOMMENDATION_LABELS)}")
    if not draft.next_step.strip():
        raise ValueError("next_step must not be empty")
    return draft


def request_intake(runner: ToolRunner) -> str:
    """The request is the input, not something to search for: the orchestrator makes this (logged) tool call
    up front and puts the result in the first message, saving a full LLM round trip."""
    return render_for_model(runner.call("get_request_context", {}, call_id="intake"))


def run_single_agent(request: dict, runner: ToolRunner, client: ChatClient) -> LoopResult[DraftDecision]:
    return run_tool_loop(
        client=client,
        system_prompt=SINGLE_AGENT_SYSTEM,
        task=single_agent_task(str(request.get("request_id") or ""), request_intake(runner)),
        runner=runner,
        tool_names=None,
        terminal_schema=submit_decision_schema(),
        parse=parse_draft,
    )
