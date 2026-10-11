"""Architecture A end-to-end with a scripted model (no API key, fully deterministic)."""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from src import llm as llm_module
from src.contracts import ProcurementDecision
from src.data_access import get_request
from src.guardrails import match_label, normalize_approval, normalize_flag
from src.llm import LLMError, LLMQuotaExhausted, LLMUnavailable, RateLimiter, ScriptedLLM
from src.local_services import start_vendor_api_in_background
from src.policy_engine import BUDGET_EXCEPTION, CLARIFY, MANUAL_REVIEW, PROCEED, SPECIALIST, USE_EXISTING
from src.solution import run_copilot

EVIDENCE_TOOLS = ["check_budget", "search_software_catalog", "get_vendor_registry_record", "get_vendor_risk", "evaluate_policy_rules"]


def gather_turns() -> list:
    """The orchestrator already made the intake call (get_request_context); the agent gathers the rest."""
    return [[(name, {}) for name in EVIDENCE_TOOLS]]


def submit(**overrides) -> list:
    args = {
        "recommendation": PROCEED,
        "rationale": "Test rationale.",
        "existing_tool_assessment": "n/a",
        "evidence": [
            {"source": "check_budget", "finding": "Within budget", "reference": "department_budgets.csv"},
            {"source": "evaluate_policy_rules", "finding": "Tier computed", "reference": "Policy section 4"},
            {"source": "get_vendor_risk", "finding": "Vendor checked", "reference": "GET /vendor-risk/x"},
            {"source": "get_request_context", "finding": "Request read", "reference": "REQ"},
            {"source": "search_software_catalog", "finding": "Catalog searched", "reference": "SW"},
            {"source": "get_vendor_registry_record", "finding": "Registry read", "reference": "V"},
        ],
        "required_approvals": [],
        "missing_information": [],
        "risk_flags": [],
        "next_step": "Route to approvers.",
    }
    args.update(overrides)
    return [("submit_decision", args)]


def scripted(*turns):
    def factory(telemetry):
        return ScriptedLLM(script=list(turns), telemetry=telemetry)
    return factory


class OfflineTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous = os.environ.get("VENDOR_RISK_BASE_URL")
        os.environ["VENDOR_RISK_BASE_URL"] = start_vendor_api_in_background()

    @classmethod
    def tearDownClass(cls):
        if cls._previous is None:
            os.environ.pop("VENDOR_RISK_BASE_URL", None)
        else:
            os.environ["VENDOR_RISK_BASE_URL"] = cls._previous

    def run_case(self, request_id, *turns):
        return run_copilot(get_request(request_id), "single", client_factory=scripted(*turns))


class HappyPathTests(OfflineTestBase):
    def test_compliant_agent(self):
        run = self.run_case("REQ-1001", *gather_turns(), submit(required_approvals=["Manager"], risk_flags=["existing_tool_overlap"]))
        d = run.decision
        self.assertIsInstance(d, ProcurementDecision)
        self.assertIsNone(run.fallback_reason)
        self.assertTrue(d.recommendation.startswith(PROCEED))
        self.assertEqual(d.required_approvals, ["Manager"])
        self.assertTrue(d.human_review_required)
        self.assertEqual(d.telemetry.llm_calls, 2)
        self.assertEqual(d.telemetry.tool_calls, 6)  # intake + 5 agent calls
        self.assertEqual(d.telemetry.tool_names[0], "get_request_context")
        self.assertEqual(run.telemetry["guardrail_overrides"], [])
        self.assertEqual(run.stages["agent"]["iterations"], 2)
        self.assertTrue(any("Manager approver: Priya Shah" in e.finding for e in d.evidence))

    def test_tool_results_reach_the_model_wrapped(self):
        llm = ScriptedLLM(script=[*gather_turns(), submit(recommendation=CLARIFY)])
        run_copilot(get_request("REQ-1006"), "single", client_factory=lambda t: llm)
        tool_messages = [m for m in llm.calls[-1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 5)
        first_user = llm.calls[0]["messages"][1]["content"]
        self.assertIn("get_request_context output", first_user)
        self.assertIn("<untrusted_data", first_user)  # injected justification arrives wrapped
        joined = "".join(m["content"] for m in tool_messages)
        self.assertIn("<untrusted_data", joined)
        self.assertEqual(llm.calls[0]["tool_choice"], "required")


class GuardrailTests(OfflineTestBase):
    def test_dropped_approvals_and_flags_are_restored(self):
        run = self.run_case("REQ-1005", *gather_turns(), submit(recommendation=BUDGET_EXCEPTION, required_approvals=["Finance"]))
        d = run.decision
        for role in ("Department Head", "Procurement", "Finance", "Security", "Privacy", "Legal"):
            self.assertIn(role, d.required_approvals)
        self.assertIn("budget_insufficient", d.risk_flags)
        overrides = run.telemetry["guardrail_overrides"]
        self.assertIn("approval_added: Security", overrides)
        self.assertIn("flag_added: budget_insufficient", overrides)

    def test_too_permissive_recommendation_is_corrected(self):
        run = self.run_case("REQ-1005", *gather_turns(), submit(recommendation=PROCEED))
        self.assertTrue(run.decision.recommendation.startswith(BUDGET_EXCEPTION))
        self.assertIn(f"recommendation_changed: {PROCEED} -> {BUDGET_EXCEPTION}", run.telemetry["guardrail_overrides"])
        self.assertIn("Finance", run.decision.next_step)

    def test_more_cautious_recommendation_is_kept(self):
        run = self.run_case("REQ-1008", *gather_turns(), submit(recommendation=USE_EXISTING, rationale="TaskFlow covers it."))
        self.assertTrue(run.decision.recommendation.startswith(USE_EXISTING))
        self.assertFalse(any(o.startswith("recommendation_changed") for o in run.telemetry["guardrail_overrides"]))

    def test_specialist_review_cannot_be_skipped(self):
        run = self.run_case("REQ-1003", *gather_turns(), submit(recommendation=PROCEED))
        self.assertTrue(run.decision.recommendation.startswith(SPECIALIST))

    def test_evidence_from_uncalled_tool_is_dropped(self):
        run = self.run_case("REQ-1001", [("get_request_context", {})], [("evaluate_policy_rules", {})],
                            submit(required_approvals=["Manager"]))
        sources = {e.source for e in run.decision.evidence}
        self.assertNotIn("check_budget", sources)
        self.assertIn("policy_guardrail", sources)  # budget finding re-added from the engine
        overrides = run.telemetry["guardrail_overrides"]
        self.assertTrue(any(o.startswith("evidence_dropped: source 'check_budget'") for o in overrides))
        self.assertIn("evidence_added: budget", overrides)

    def test_contradicted_missing_information_is_dropped(self):
        run = self.run_case("REQ-1001", *gather_turns(),
                            submit(required_approvals=["Manager"], missing_information=["Annual cost", "Vendor SOC 2 report"]))
        self.assertEqual(run.decision.missing_information, [])  # complete request: follow-ups become open questions
        self.assertIn("Vendor SOC 2 report", run.decision.next_step)
        self.assertNotIn("Annual cost", run.decision.next_step)
        self.assertTrue(any(o.startswith("missing_dropped: 'Annual cost'") for o in run.telemetry["guardrail_overrides"]))

    def test_follow_up_questions_are_not_mistaken_for_present_fields(self):
        question = ("What personal data, if any, BrandBoard would process given the request declares only "
                    "'internal_marketing' data access")
        run = self.run_case("REQ-1002", *gather_turns(),
                            submit(recommendation=SPECIALIST, missing_information=[question, "Number of users"]))
        self.assertIn(question, run.decision.next_step)  # kept, as an open question (request is complete)
        self.assertNotIn("Number of users", run.decision.next_step)  # user_count is present: dropped

    def test_model_follow_up_questions_do_not_force_clarification(self):
        """PUB-06 finding: a correct 'Manual review' must not become 'Request clarification' because the
        model listed follow-up questions about the vendor (the request itself is complete)."""
        run = self.run_case("REQ-1009", *gather_turns(),
                            submit(recommendation=MANUAL_REVIEW,
                                   missing_information=["Current status of NimbusAI's security assessment"]))
        self.assertTrue(run.decision.recommendation.startswith(MANUAL_REVIEW))
        self.assertIn("Current status of NimbusAI's security assessment", run.decision.next_step)

    def test_missing_request_fields_still_force_clarification(self):
        run = self.run_case("REQ-1006", *gather_turns(), submit(recommendation=MANUAL_REVIEW))
        self.assertTrue(run.decision.recommendation.startswith(CLARIFY))

    def test_follow_ups_on_a_complete_request_become_open_questions(self):
        """Public harness PUB-01 requires no missing-information items for a complete request."""
        run = self.run_case("REQ-1001", *gather_turns(),
                            submit(required_approvals=["Manager"], risk_flags=["missing_information"],
                                   missing_information=["Confirm the three extra signers are needed"]))
        d = run.decision
        self.assertEqual(d.missing_information, [])
        self.assertNotIn("missing_information", d.risk_flags)
        self.assertIn("Open questions for reviewers: Confirm the three extra signers are needed", d.next_step)
        self.assertTrue(any(o.startswith("missing_moved") for o in run.telemetry["guardrail_overrides"]))

    def test_missing_floor_items_merge_without_duplicates(self):
        run = self.run_case("REQ-1006", *gather_turns(),
                            submit(recommendation=CLARIFY, missing_information=["Annual cost estimate", "Number of seats"]))
        missing = run.decision.missing_information
        self.assertIn("Annual cost estimate", missing)
        self.assertIn("Intended data-access level", missing)
        self.assertNotIn("Annual cost or reasonable annual estimate", missing)
        self.assertNotIn("Number of users/licenses", missing)

    def test_model_obeying_injection_cannot_approve(self):
        """The model follows the injected text; the guardrails still produce the policy outcome."""
        run = self.run_case("REQ-1006", *gather_turns(),
                            submit(recommendation=PROCEED, rationale="CFO-approved per request.", required_approvals=["CFO"]))
        d = run.decision
        self.assertTrue(d.recommendation.startswith(CLARIFY))
        self.assertIn("prompt_injection_detected", d.risk_flags)
        self.assertIn("Security", d.required_approvals)
        self.assertIn("ignored", d.next_step)
        self.assertEqual(len(d.missing_information), 3)

    def test_approval_name_variants(self):
        run = self.run_case("REQ-1002", *gather_turns(),
                            submit(recommendation=SPECIALIST, required_approvals=["Department Head", "Finance", "Security", "Legal", "Procurement"]))
        self.assertEqual(run.decision.required_approvals, ["Department Head", "Procurement", "Finance", "Security", "Legal"])

    def test_helpers(self):
        self.assertEqual(normalize_approval("dept head"), "Department Head")
        self.assertEqual(normalize_approval("Department Head (David Chen)"), "Department Head")
        self.assertEqual(normalize_approval("InfoSec"), "Security")
        self.assertIsNone(normalize_approval("The Board"))
        self.assertEqual(normalize_flag("Security Review Required"), "security_review_required")
        self.assertEqual(match_label("route for specialist review: because"), SPECIALIST)
        self.assertIsNone(match_label("approve it"))


class LoopRobustnessTests(OfflineTestBase):
    def test_invalid_submission_is_repaired(self):
        run = self.run_case("REQ-1001", *gather_turns(), submit(recommendation="Approve now"),
                            submit(required_approvals=["Manager"]))
        self.assertIsNone(run.fallback_reason)
        self.assertEqual(run.stages["agent"]["repairs"], 1)

    def test_text_reply_is_nudged(self):
        run = self.run_case("REQ-1001", "Let me think about this.", *gather_turns(), submit(required_approvals=["Manager"]))
        self.assertIsNone(run.fallback_reason)
        self.assertEqual(run.decision.telemetry.llm_calls, 3)  # text, gather, submit

    def test_decision_written_as_text_json_is_recovered(self):
        args = submit(required_approvals=["Manager"])[0][1]
        text = "Here is my decision:\n```json\n" + json.dumps(args) + "\n```"
        run = self.run_case("REQ-1001", *gather_turns(), text)
        self.assertIsNone(run.fallback_reason)
        self.assertTrue(run.stages["agent"]["recovered_from_text"])

    def test_unknown_tool_and_bad_args_do_not_crash(self):
        run = self.run_case("REQ-1001", [("no_such_tool", {}), ("lookup_policy_section", "{bad")], *gather_turns(),
                            submit(required_approvals=["Manager"]))
        self.assertIsNone(run.fallback_reason)
        self.assertEqual(len(run.telemetry["errors"]), 2)

    def test_never_submitting_falls_back(self):
        run = self.run_case("REQ-1005", *([[("get_request_context", {})]] * 10))
        self.assertIn("AgentLoopError", run.fallback_reason)
        self.assertIn("llm_unavailable", run.decision.risk_flags)
        self.assertTrue(run.decision.recommendation.startswith(BUDGET_EXCEPTION))

    def test_llm_error_falls_back_conservatively(self):
        run = self.run_case("REQ-1009", [("get_request_context", {})], LLMError("provider down"))
        d = run.decision
        self.assertIn("llm_unavailable", d.risk_flags)
        self.assertIn("vendor_risk_unavailable", d.risk_flags)
        self.assertIn("Security", d.required_approvals)
        self.assertEqual(d.telemetry.tool_calls, 2)  # intake + one agent call

    def test_no_api_key_falls_back(self):
        def factory(telemetry):
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        run = run_copilot(get_request("REQ-1001"), "single", client_factory=factory)
        self.assertIn("LLMUnavailable", run.fallback_reason)
        self.assertEqual(run.decision.required_approvals, ["Manager"])
        self.assertGreaterEqual(len(run.decision.evidence), 2)

    def test_unknown_architecture_falls_through_to_error(self):
        with self.assertRaises(ValueError):
            run_copilot(get_request("REQ-1001"), "triple", client_factory=scripted())


def evidence_pack(**overrides) -> list:
    args = {
        "request_summary": "Requester wants a tool.",
        "key_findings": [{"source": "check_budget", "finding": "Within budget", "reference": "department_budgets.csv"},
                         {"source": "evaluate_policy_rules", "finding": "Tier computed", "reference": "Policy section 4"}],
        "existing_tool_assessment": "No existing tool covers it.",
        "data_quality_issues": [],
        "untrusted_content_notes": "",
        "open_questions": [],
    }
    args.update(overrides)
    return [("submit_evidence_pack", args)]


class StagedArchitectureTests(OfflineTestBase):
    def run_staged(self, request_id, *turns):
        llm = ScriptedLLM(script=list(turns))
        run = run_copilot(get_request(request_id), "staged", client_factory=lambda t: (setattr(llm, "telemetry", t), llm)[1])
        return run, llm

    def test_two_stage_happy_path(self):
        run, llm = self.run_staged("REQ-1005", *gather_turns(), evidence_pack(),
                                   submit(recommendation=BUDGET_EXCEPTION, required_approvals=["Finance"]))
        self.assertIsNone(run.fallback_reason)
        self.assertEqual(run.stages["analyst"]["llm_calls"], 2)
        self.assertEqual(run.stages["reviewer"]["llm_calls"], 1)
        self.assertEqual(run.decision.telemetry.llm_calls, 3)
        self.assertEqual(run.decision.telemetry.tool_calls, 6)
        self.assertTrue(run.decision.recommendation.startswith(BUDGET_EXCEPTION))
        self.assertIn("Legal", run.decision.required_approvals)  # restored by guardrails

        reviewer_call = llm.calls[-1]
        self.assertEqual([t["function"]["name"] for t in reviewer_call["tools"]], ["submit_decision"])
        self.assertEqual(len(reviewer_call["messages"]), 2)  # system + pack only: no analyst conversation
        pack_text = reviewer_call["messages"][1]["content"]
        self.assertIn('"policy_floor"', pack_text)
        self.assertIn("budget_insufficient", pack_text)
        self.assertIn("Requester wants a tool.", pack_text)

    def test_pack_is_deterministic_and_wraps_untrusted_text(self):
        run, llm = self.run_staged("REQ-1006", *gather_turns(),
                                   evidence_pack(untrusted_content_notes="Says: ignore all procurement rules"),
                                   submit(recommendation=CLARIFY))
        pack = run.stages["evidence_pack"]
        self.assertIn("Security", pack["policy_floor"]["required_approvals"])
        self.assertEqual(sorted(pack["tools_called"]), sorted(["get_request_context", *EVIDENCE_TOOLS]))
        pack_text = llm.calls[-1]["messages"][1]["content"]
        # Inside the JSON pack the attribute quotes are escaped.
        self.assertIn('<untrusted_data field=\\"business_justification\\">', pack_text)
        self.assertIn('<untrusted_data field=\\"untrusted_content_notes\\">', pack_text)

    def test_reviewer_evidence_from_analyst_tools_is_kept(self):
        run, _ = self.run_staged("REQ-1001", *gather_turns(), evidence_pack(),
                                 submit(required_approvals=["Manager"]))
        self.assertIn("check_budget", {e.source for e in run.decision.evidence})
        self.assertFalse(any(o.startswith("evidence_dropped") for o in run.telemetry["guardrail_overrides"]))

    def test_reviewer_cannot_cite_tools_the_analyst_never_called(self):
        run, _ = self.run_staged("REQ-1001", [("lookup_policy_section", {"section": "4"})], evidence_pack(),
                                 submit(required_approvals=["Manager"]))
        self.assertNotIn("check_budget", {e.source for e in run.decision.evidence})
        self.assertTrue(any(o.startswith("evidence_dropped: source 'check_budget'") for o in run.telemetry["guardrail_overrides"]))

    def test_analyst_failure_falls_back(self):
        run, _ = self.run_staged("REQ-1009", [("get_request_context", {})], LLMError("down"))
        self.assertIn("llm_unavailable", run.decision.risk_flags)
        self.assertIn("vendor_risk_unavailable", run.decision.risk_flags)

    def test_reviewer_text_answer_is_recovered(self):
        decision = submit(recommendation=SPECIALIST, required_approvals=["Security"])[0][1]
        text = "```json\n" + json.dumps(decision) + "\n```"
        run, _ = self.run_staged("REQ-1003", *gather_turns(), evidence_pack(), text)
        self.assertIsNone(run.fallback_reason)
        self.assertTrue(run.stages["reviewer"]["recovered_from_text"])

    def test_reviewer_never_submitting_falls_back(self):
        run, _ = self.run_staged("REQ-1003", *gather_turns(), evidence_pack(), "thinking", "still thinking", "hmm", "no")
        self.assertIn("AgentLoopError", run.fallback_reason)
        self.assertTrue(run.decision.recommendation.startswith(SPECIALIST))


class ClientHelperTests(unittest.TestCase):
    def fake_exc(self, body=None, text=""):
        exc = Exception(text)
        exc.body = body
        return exc

    def test_recover_tool_call_from_failed_generation(self):
        exc = self.fake_exc({"error": {"code": "tool_use_failed",
                                       "failed_generation": '{"name": "check_budget", "arguments": {"annual_cost_usd": null}}'}})
        response = llm_module._recover_failed_tool_call(exc)
        self.assertEqual(response.tool_calls[0].name, "check_budget")
        self.assertEqual(json.loads(response.tool_calls[0].arguments), {"annual_cost_usd": None})

    def test_recover_prose_as_text(self):
        exc = self.fake_exc({"error": {"code": "tool_use_failed", "failed_generation": "**Evidence** ..."}})
        response = llm_module._recover_failed_tool_call(exc)
        self.assertEqual(response.tool_calls, [])
        self.assertIn("Evidence", response.content)

    def test_bare_submission_json_is_returned_as_text(self):
        bare = json.dumps({"recommendation": "Proceed to approval routing", "next_step": "x"})
        response = llm_module._recover_failed_tool_call(self.fake_exc({"error": {"code": "tool_use_failed", "failed_generation": bare}}))
        self.assertEqual(response.tool_calls, [])
        self.assertEqual(json.loads(response.content)["recommendation"], "Proceed to approval routing")

    def test_other_errors_not_recovered(self):
        self.assertIsNone(llm_module._recover_failed_tool_call(self.fake_exc({"error": {"code": "invalid_api_key"}})))
        self.assertIsNone(llm_module._recover_failed_tool_call(self.fake_exc(None)))

    def test_quota_detection(self):
        self.assertTrue(llm_module._is_quota_exhausted(self.fake_exc(text="GenerateRequestsPerDayPerProjectPerModel 'retryDelay': '47783s'")))
        self.assertTrue(llm_module._is_quota_exhausted(self.fake_exc(text="Rate limit reached on tokens per day (TPD)")))
        self.assertFalse(llm_module._is_quota_exhausted(self.fake_exc(text="Please retry in 7.5s")))
        self.assertEqual(llm_module._backoff_seconds(self.fake_exc(text="Please retry in 7.5s"), 0), 8.0)

    def test_rate_limiter_waits_when_window_full(self):
        limiter = RateLimiter(2)
        self.assertEqual(limiter.acquire(), 0)
        self.assertEqual(limiter.acquire(), 0)
        with mock.patch("src.llm.time.sleep") as sleep, mock.patch("src.llm.time.monotonic", side_effect=[100.0, 160.0]):
            limiter._calls.clear()
            limiter._calls.extend([99.0, 99.5])
            waited = limiter.acquire()
        self.assertGreater(waited, 0)
        sleep.assert_called_once()

    def test_missing_key_raises_unavailable(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "groq", "GROQ_API_KEY": ""}):
            with self.assertRaises(LLMUnavailable):
                llm_module.OpenAICompatibleClient()

    def test_quota_error_is_raised_without_retrying(self):
        import openai
        client = object.__new__(llm_module.OpenAICompatibleClient)
        client._openai = openai
        client.provider, client.model, client.max_retries, client.reasoning_effort = "gemini", "m", 4, None
        client.telemetry = __import__("src.telemetry", fromlist=["x"]).RunTelemetryCounter()
        client._limiter = RateLimiter(1000)
        error = openai.RateLimitError("Quota exceeded GenerateRequestsPerDayPerProjectPerModel retryDelay': '47783s'",
                                      response=mock.Mock(status_code=429, headers={}), body=None)
        client._client = mock.Mock()
        client._client.chat.completions.create.side_effect = error
        with mock.patch("src.llm.time.sleep") as sleep:
            with self.assertRaises(LLMQuotaExhausted):
                client.chat([{"role": "user", "content": "hi"}])
        sleep.assert_not_called()
        self.assertEqual(client._client.chat.completions.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
