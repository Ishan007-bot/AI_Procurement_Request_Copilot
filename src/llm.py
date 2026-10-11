"""LLM client for Gemini and Groq via their OpenAI-compatible endpoints.

* A process-wide sliding-window limiter keeps calls inside free-tier
  requests-per-minute limits; time spent waiting is reported separately so
  latency comparisons stay fair.
* 429 / 5xx / timeouts / connection errors are retried with backoff
  (honouring `retry-after` when the provider sends it).
* If a provider rejects a forced `tool_choice`, the client falls back to
  "auto" for the rest of the process and the agent loop nudges instead.
"""
from __future__ import annotations

import collections
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from src import config
from src.telemetry import RunTelemetryCounter


class LLMUnavailable(RuntimeError):
    """No usable model (missing key, unsupported provider, SDK missing)."""


class LLMError(RuntimeError):
    """The provider failed after retries or returned something unusable."""


class LLMQuotaExhausted(LLMError):
    """Free-tier daily quota used up; retrying now cannot succeed."""


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[LLMToolCall]
    assistant_message: dict  # to append to the conversation verbatim
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class ChatClient(Protocol):
    provider: str
    model: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: Any = "auto") -> LLMResponse: ...


class RateLimiter:
    """Sliding 60-second window shared by every run in the process."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max(1, max_per_minute)
        self._calls: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a call is allowed; return milliseconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60:
                    self._calls.popleft()
                if len(self._calls) < self.max_per_minute:
                    self._calls.append(now)
                    return waited * 1000
                delay = 60 - (now - self._calls[0]) + 0.05
            time.sleep(delay)
            waited += delay


_limiters: dict[tuple[str, int], RateLimiter] = {}
_forced_tool_choice_unsupported: set[str] = set()
_reasoning_unsupported: set[str] = set()


def _limiter(provider: str, rpm: int) -> RateLimiter:
    key = (provider, rpm)
    if key not in _limiters:
        _limiters[key] = RateLimiter(rpm)
    return _limiters[key]


MAX_RETRY_WAIT_SECONDS = 90.0
MAX_THROTTLE_WAITS = 15


def clean_tool_name(name: str) -> str:
    """gpt-oss can leak harmony channel tags into names, e.g. 'search_catalog<|channel|>commentary'."""
    return re.split(r"<\|", str(name or ""), maxsplit=1)[0].strip()


def _requested_retry_seconds(exc: Exception) -> float | None:
    """Retry delay the provider asked for: `retry-after` header or Google's RetryInfo in the body."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        if value is not None:
            return float(value)
    except ValueError:
        pass
    text = str(exc)
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", text)
    if match:
        return float(match.group(1))
    match = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s", text)
    if match:
        hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def _is_quota_exhausted(exc: Exception) -> bool:
    text = str(exc)
    requested = _requested_retry_seconds(exc)
    return "PerDay" in text or "per day" in text.lower() or (requested is not None and requested > MAX_RETRY_WAIT_SECONDS)


def _backoff_seconds(exc: Exception, attempt: int) -> float:
    requested = _requested_retry_seconds(exc)
    if requested is not None:
        return min(requested + 0.5, MAX_RETRY_WAIT_SECONDS)
    return min(2.0 * (2 ** attempt), 60.0)


def _error_body(exc: Exception) -> dict:
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _error_code(exc: Exception) -> str | None:
    return _error_body(exc).get("code")


def _recover_failed_tool_call(exc: Exception) -> LLMResponse | None:
    """Rebuild tool call(s) from a provider `tool_use_failed` error, if present."""
    error = _error_body(exc)
    if error.get("code") != "tool_use_failed":
        return None
    text = error.get("failed_generation") or ""
    try:
        generation = json.loads(text)
    except ValueError:
        # The model answered in prose instead of calling a tool; hand the text to the agent loop.
        return LLMResponse(content=text, tool_calls=[], assistant_message={"role": "assistant", "content": text},
                           finish_reason="stop") if text.strip() else None
    items = generation if isinstance(generation, list) else [generation]
    if not all(isinstance(item, dict) and item.get("name") for item in items):
        # JSON, but not a tool call (e.g. the bare arguments of the final submit tool): treat as a text answer,
        # which the agent loop can still recover if it is a complete, valid submission.
        return LLMResponse(content=text, tool_calls=[], assistant_message={"role": "assistant", "content": text},
                           finish_reason="stop")
    calls: list[LLMToolCall] = []
    for i, item in enumerate(items):
        arguments = item.get("arguments", item.get("parameters", {}))
        calls.append(LLMToolCall(id=f"recovered_{int(time.time() * 1000)}_{i}", name=clean_tool_name(str(item["name"])),
                                 arguments=arguments if isinstance(arguments, str) else json.dumps(arguments)))
    message = {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]}
    return LLMResponse(content=None, tool_calls=calls, assistant_message=message, finish_reason="tool_calls")


class OpenAICompatibleClient:
    def __init__(self, telemetry: RunTelemetryCounter | None = None):
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise LLMUnavailable("The 'openai' package is not installed. Run: pip install -r requirements.txt") from exc
        self._openai = openai
        self.provider = config.get_llm_provider()
        settings = config.get_provider_settings()
        api_key = config.get_llm_api_key()
        if not api_key:
            raise LLMUnavailable(f"{settings['api_key_env']} is not set. Add it to .env (see .env.example).")
        self.model = config.get_model_name()
        self.telemetry = telemetry or RunTelemetryCounter()
        self.max_retries = config.get_llm_max_retries()
        self.reasoning_effort = config.get_llm_reasoning_effort()
        self._client = openai.OpenAI(base_url=settings["base_url"], api_key=api_key,
                                     timeout=config.get_llm_timeout(), max_retries=0)
        self._limiter = _limiter(self.provider, config.get_llm_max_rpm())

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: Any = "auto") -> LLMResponse:
        openai = self._openai
        if tool_choice not in (None, "auto", "none") and self.provider in _forced_tool_choice_unsupported:
            tool_choice = "auto"
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if self.reasoning_effort and self.model not in _reasoning_unsupported:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        last_error: Exception | None = None
        attempt = 0          # real errors (5xx, timeouts, unparseable output); limited by LLM_MAX_RETRIES
        throttle_waits = 0   # per-minute rate limits: expected on the free tier, waited out separately
        while attempt <= self.max_retries:
            waited_ms = self._limiter.acquire()
            started = time.perf_counter()
            try:
                completion = self._client.chat.completions.create(**kwargs)
            except openai.BadRequestError as exc:
                recovered = _recover_failed_tool_call(exc)
                if recovered is not None:
                    # Groq validates tool arguments server-side and rejects e.g. null for an optional number.
                    # The intended call is in `failed_generation`; our own validator decides what is acceptable.
                    self.telemetry.record_llm_call(latency_ms=(time.perf_counter() - started) * 1000, throttle_wait_ms=waited_ms)
                    what = recovered.tool_calls[0].name if recovered.tool_calls else "text reply"
                    self.telemetry.record_error(f"llm: recovered provider-rejected output ({what})")
                    return recovered
                if _error_code(exc) == "output_parse_failed" and attempt < self.max_retries:
                    # gpt-oss on Groq sometimes emits its plan as text where a tool call was required.
                    # Retry this turn with tool_choice="auto"; the agent loop nudges if it answers in text.
                    attempt += 1
                    self.telemetry.record_llm_call(latency_ms=(time.perf_counter() - started) * 1000, throttle_wait_ms=waited_ms)
                    self.telemetry.record_error("llm: provider could not parse the model's tool call; retrying with tool_choice=auto")
                    if tools:
                        kwargs["tool_choice"] = "auto"
                    continue
                if "reasoning_effort" in kwargs and "reasoning" in str(exc).lower():
                    # Model doesn't accept reasoning_effort: drop it for this model from now on.
                    _reasoning_unsupported.add(self.model)
                    kwargs.pop("reasoning_effort")
                    self.telemetry.throttle_wait_ms += waited_ms
                    continue
                if tools and kwargs.get("tool_choice") not in ("auto", None) and "tool_choice" in str(exc).lower():
                    _forced_tool_choice_unsupported.add(self.provider)
                    kwargs["tool_choice"] = "auto"
                    self.telemetry.throttle_wait_ms += waited_ms
                    continue
                raise LLMError(f"{self.provider} rejected the request: {exc}") from exc
            except openai.RateLimitError as exc:
                last_error = exc
                self.telemetry.throttle_wait_ms += waited_ms
                if _is_quota_exhausted(exc):
                    raise LLMQuotaExhausted(f"{self.provider} {self.model} free-tier quota exhausted: "
                                            f"{str(exc).splitlines()[0][:300]}") from exc
                throttle_waits += 1
                self.telemetry.record_error(f"llm rate limited (wait {throttle_waits})")
                if throttle_waits > MAX_THROTTLE_WAITS:
                    raise LLMError(f"{self.provider} still rate limited after {MAX_THROTTLE_WAITS} waits: {exc}") from exc
                backoff = _backoff_seconds(exc, min(throttle_waits, 4))
                self.telemetry.throttle_wait_ms += backoff * 1000
                time.sleep(backoff)
                continue
            except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
                last_error = exc
                attempt += 1
                self.telemetry.throttle_wait_ms += waited_ms
                self.telemetry.record_error(f"llm attempt {attempt}: {type(exc).__name__}")
                if isinstance(exc, openai.APITimeoutError):
                    # A timed-out request was active model time, not throttling.
                    self.telemetry.llm_latency_ms += (time.perf_counter() - started) * 1000
                if attempt <= self.max_retries:
                    backoff = _backoff_seconds(exc, attempt)
                    self.telemetry.throttle_wait_ms += backoff * 1000
                    time.sleep(backoff)
                continue
            except openai.APIError as exc:
                raise LLMError(f"{self.provider} API error: {exc}") from exc

            latency_ms = (time.perf_counter() - started) * 1000
            usage = getattr(completion, "usage", None)
            self.telemetry.record_llm_call(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                latency_ms=latency_ms, throttle_wait_ms=waited_ms,
            )
            if not completion.choices:
                raise LLMError(f"{self.provider} returned no choices")
            choice = completion.choices[0]
            message = choice.message
            calls = [LLMToolCall(id=tc.id or f"call_{i}", name=clean_tool_name(tc.function.name),
                                 arguments=tc.function.arguments or "{}")
                     for i, tc in enumerate(message.tool_calls or [])]
            # model_dump keeps provider extras (e.g. Gemini thought signatures) needed when echoing the turn back.
            assistant = message.model_dump(exclude_none=True)
            assistant["role"] = "assistant"
            assistant.setdefault("content", message.content or "")
            for i, tc in enumerate(assistant.get("tool_calls", [])):
                tc["id"] = calls[i].id
                tc.setdefault("function", {})["name"] = calls[i].name
            return LLMResponse(content=message.content, tool_calls=calls, assistant_message=assistant,
                               finish_reason=choice.finish_reason,
                               input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                               output_tokens=getattr(usage, "completion_tokens", 0) or 0)
        raise LLMError(f"{self.provider} failed after {attempt} attempts: {last_error}")


def get_llm_client(telemetry: RunTelemetryCounter | None = None) -> ChatClient:
    return OpenAICompatibleClient(telemetry)


@dataclass
class ScriptedLLM:
    """Deterministic stand-in for tests and offline demos.

    `script` is a list of turns; each turn is a list of (tool_name, arguments)
    pairs or a plain string (a text-only reply). Calls are recorded.
    """
    script: list[Any]
    telemetry: RunTelemetryCounter | None = None
    provider: str = "scripted"
    model: str = "scripted"
    calls: list[dict] = field(default_factory=list)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: Any = "auto") -> LLMResponse:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice})
        if self.telemetry is not None:
            self.telemetry.record_llm_call(input_tokens=100, output_tokens=50)
        if not self.script:
            raise LLMError("ScriptedLLM ran out of turns")
        turn = self.script.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            return LLMResponse(content=turn, tool_calls=[], assistant_message={"role": "assistant", "content": turn})
        calls = [LLMToolCall(id=f"call_{len(self.calls)}_{i}", name=name,
                             arguments=args if isinstance(args, str) else json.dumps(args))
                 for i, (name, args) in enumerate(turn)]
        message = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]}
        return LLMResponse(content=None, tool_calls=calls, assistant_message=message)
