"""Tool-calling loop shared by every agent.

The model works through tools until it calls a terminal tool (e.g.
submit_decision). The terminal tool's arguments are validated with a parser;
invalid output gets one or more repair turns. Every tool call gets a tool
reply, as the OpenAI-compatible protocol requires.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

from pydantic import ValidationError

from src.llm import ChatClient, LLMError
from src.prompts import NUDGE_CALL_TOOL
from src.tools import ToolRunner, render_for_model

T = TypeVar("T")

MAX_ITERATIONS = 10
MAX_REPAIRS = 2


class AgentLoopError(RuntimeError):
    """The agent did not produce a valid terminal output."""


@dataclass
class LoopResult(Generic[T]):
    output: T
    iterations: int
    repairs: int
    transcript: list[dict] = field(default_factory=list)
    recovered_from_text: bool = False


def extract_json_object(text: str, required_keys: list[str]) -> dict | None:
    """Find a JSON object in free text (e.g. a ```json block) that has every required key."""
    decoder = json.JSONDecoder()
    found: dict | None = None
    for start in [i for i, ch in enumerate(text or "") if ch == "{"]:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and all(k in obj for k in required_keys):
            found = obj  # keep the last complete match (models restate their final answer at the end)
    return found


def run_tool_loop(
    client: ChatClient,
    system_prompt: str,
    task: str,
    runner: ToolRunner | None,
    tool_names: list[str] | None,
    terminal_schema: dict,
    parse: Callable[[dict], T],
    max_iterations: int = MAX_ITERATIONS,
) -> LoopResult[T]:
    terminal_name = terminal_schema["function"]["name"]
    required_keys = terminal_schema["function"]["parameters"].get("required", [])
    tools = (runner.schemas(tool_names) if runner is not None and tool_names != [] else []) + [terminal_schema]
    messages: list[dict] = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task}]
    repairs = 0

    for iteration in range(1, max_iterations + 1):
        tool_choice = "required" if iteration < max_iterations else {"type": "function", "function": {"name": terminal_name}}
        response = client.chat(messages, tools=tools, tool_choice=tool_choice)
        messages.append(response.assistant_message)

        if not response.tool_calls:
            # Some models write the final answer as text/JSON instead of calling the terminal tool.
            candidate = extract_json_object(response.content or "", required_keys)
            if candidate is not None:
                try:
                    output = parse(candidate)
                except (ValueError, ValidationError) as exc:
                    repairs += 1
                    if repairs > MAX_REPAIRS:
                        raise AgentLoopError(f"{terminal_name} output still invalid after {MAX_REPAIRS} repairs: {exc}") from exc
                    messages.append({"role": "user", "content": f"Your answer was invalid ({str(exc).splitlines()[0][:300]}). "
                                                                f"Call the {terminal_name} tool with corrected arguments."})
                    continue
                return LoopResult(output=output, iterations=iteration, repairs=repairs, transcript=messages,
                                  recovered_from_text=True)
            messages.append({"role": "user", "content": NUDGE_CALL_TOOL})
            continue

        terminal_calls = [c for c in response.tool_calls if c.name == terminal_name]
        for call in response.tool_calls:
            if call.name == terminal_name:
                continue
            if runner is None:
                content = json.dumps({"error": f"Tool '{call.name}' is not available to this agent"})
            else:
                content = render_for_model(runner.call(call.name, call.arguments, call_id=call.id))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

        if not terminal_calls:
            continue
        call = terminal_calls[0]
        try:
            arguments = json.loads(call.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
            output = parse(arguments)
        except (ValueError, ValidationError) as exc:
            repairs += 1
            problem = str(exc).splitlines()[0][:400]
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps({"error": f"Invalid {terminal_name} arguments: {problem}. Fix and call {terminal_name} again."})})
            for extra in terminal_calls[1:]:
                messages.append({"role": "tool", "tool_call_id": extra.id, "content": json.dumps({"error": "duplicate call ignored"})})
            if repairs > MAX_REPAIRS:
                raise AgentLoopError(f"{terminal_name} output still invalid after {MAX_REPAIRS} repairs: {problem}")
            continue
        return LoopResult(output=output, iterations=iteration, repairs=repairs, transcript=messages)

    raise AgentLoopError(f"No valid {terminal_name} call within {max_iterations} iterations")


__all__ = ["AgentLoopError", "LLMError", "LoopResult", "run_tool_loop", "MAX_ITERATIONS"]
