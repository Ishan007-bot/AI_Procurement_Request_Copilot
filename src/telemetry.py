from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.contracts import RunTelemetry


@dataclass
class RunTelemetryCounter:
    """Per-run observability.

    Only llm_calls / tool_calls / tool_names go into the output contract; the
    rest is reported by the evaluation scripts. Tool calls made by the
    guardrail layer are counted as `guardrail_checks`, not as agent tool calls,
    so the comparison reflects what each architecture's agents actually did.
    """
    llm_calls: int = 0
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    llm_latency_ms: float = 0.0
    throttle_wait_ms: float = 0.0
    tool_latency_ms: float = 0.0
    guardrail_checks: int = 0
    guardrail_overrides: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)

    def record_llm_call(self, input_tokens: int = 0, output_tokens: int = 0,
                        latency_ms: float = 0.0, throttle_wait_ms: float = 0.0) -> None:
        self.llm_calls += 1
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0
        self.llm_latency_ms += latency_ms
        self.throttle_wait_ms += throttle_wait_ms

    def record_tool_call(self, name: str, latency_ms: float = 0.0) -> None:
        self.tool_calls += 1
        self.tool_names.append(name)
        self.tool_latency_ms += latency_ms

    def record_guardrail_check(self) -> None:
        self.guardrail_checks += 1

    def record_override(self, description: str) -> None:
        self.guardrail_overrides.append(description)

    def record_error(self, description: str) -> None:
        self.errors.append(description)

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    @property
    def active_latency_ms(self) -> float:
        """Wall-clock time minus free-tier throttle waits (fair A vs B latency)."""
        return max(0.0, self.elapsed_ms - self.throttle_wait_ms)

    def to_contract(self) -> RunTelemetry:
        return RunTelemetry(llm_calls=self.llm_calls, tool_calls=self.tool_calls, tool_names=list(self.tool_names))

    def summary(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tool_names": list(self.tool_names),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "llm_latency_ms": round(self.llm_latency_ms, 1),
            "tool_latency_ms": round(self.tool_latency_ms, 1),
            "throttle_wait_ms": round(self.throttle_wait_ms, 1),
            "elapsed_ms": round(self.elapsed_ms, 1),
            "active_latency_ms": round(self.active_latency_ms, 1),
            "guardrail_checks": self.guardrail_checks,
            "guardrail_overrides": list(self.guardrail_overrides),
            "errors": list(self.errors),
        }
