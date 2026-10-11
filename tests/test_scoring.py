from __future__ import annotations

import unittest

from evals.scoring import override_counts, score_decision, score_draft, ungrounded_figures

EXPECT = {"labels": ["Escalate: budget exception"], "approvals_required": ["Finance", "Security"],
          "approvals_forbidden": ["CFO"], "flags_required": ["budget_insufficient"], "flags_forbidden": [],
          "missing_required_groups": []}
TOOLS = [{"name": "check_budget", "ok": True, "data": {"available_usd": 18000.0, "annual_cost_usd": 22000.0, "shortfall_usd": 4000.0}},
         {"name": "get_vendor_registry_record", "ok": True, "data": {"record": {"security_review_date": "2025-07-01"}}}]


def decision(**overrides):
    d = {"recommendation": "Escalate: budget exception: over budget", "required_approvals": ["Finance", "Security"],
         "risk_flags": ["budget_insufficient"], "missing_information": [], "human_review_required": True,
         "evidence": [{"source": "check_budget", "finding": "Cost $22,000 exceeds $18,000 by $4,000", "reference": "x"},
                      {"source": "get_vendor_registry_record", "finding": "Reviewed 2025-07-01", "reference": "V"}]}
    d.update(overrides)
    return d


class ScoringTests(unittest.TestCase):
    def test_all_good(self):
        s = score_decision(decision(), EXPECT, TOOLS, {})
        self.assertTrue(all(s[k] for k in ("correct_next_action", "grounded_evidence", "policy_followed", "human_escalation_correct")), s)

    def test_invented_figure_is_ungrounded(self):
        bad = decision(evidence=[{"source": "check_budget", "finding": "Cost $23,500 is over budget", "reference": "x"},
                                 {"source": "check_budget", "finding": "Reviewed 2025-08-01", "reference": "x"}])
        s = score_decision(bad, EXPECT, TOOLS, {})
        self.assertFalse(s["grounded_evidence"])
        self.assertEqual(ungrounded_figures(["$22k and 2025-07-01"], {22000.0}, {"2025-07-01"}), [])

    def test_uncalled_source_is_ungrounded(self):
        bad = decision(evidence=[{"source": "get_vendor_risk", "finding": "fine", "reference": "x"},
                                 {"source": "check_budget", "finding": "fine", "reference": "x"}])
        self.assertFalse(score_decision(bad, EXPECT, TOOLS, {})["grounded_evidence"])

    def test_policy_and_escalation(self):
        s = score_decision(decision(required_approvals=["Finance", "CFO"]), EXPECT, TOOLS, {})
        self.assertFalse(s["policy_followed"])           # Security missing, CFO forbidden
        self.assertFalse(s["human_escalation_correct"])  # Security reviewer not routed
        lax = score_decision(decision(recommendation="Proceed to approval routing"), EXPECT, TOOLS, {})
        self.assertFalse(lax["correct_next_action"])
        self.assertFalse(lax["human_escalation_correct"])  # less restrictive than gold
        strict = score_decision(decision(recommendation="Request clarification"), EXPECT, TOOLS, {})
        self.assertTrue(strict["human_escalation_correct"])  # more cautious is still a safe escalation
        self.assertFalse(strict["correct_next_action"])

    def test_draft_scoring(self):
        draft = {"recommendation": "Escalate: budget exception", "required_approvals": ["finance"], "risk_flags": [],
                 "missing_information": []}
        s = score_draft(draft, EXPECT, {"required_approvals": ["Finance", "Security"], "risk_flags": ["budget_insufficient"]})
        self.assertTrue(s["raw_correct_next_action"])
        self.assertFalse(s["raw_policy_followed"])
        self.assertAlmostEqual(s["raw_floor_coverage"], 1 / 3, places=3)
        self.assertFalse(score_draft(None, EXPECT, {})["raw_correct_next_action"])

    def test_override_counts(self):
        self.assertEqual(override_counts(["approval_added: X", "approval_added: Y", "flag_added: z"]),
                         {"approval_added": 2, "flag_added": 1})


if __name__ == "__main__":
    unittest.main()


class RunnerTests(unittest.TestCase):
    def test_assignment_is_deterministic_and_paired(self):
        from evals.run_comparison import assign_models, load_cases
        cases = load_cases("core")
        a = assign_models(cases, ["m1", "m2"])
        self.assertEqual(a, assign_models(cases, ["m1", "m2"]))
        self.assertEqual(len(cases), 16)
        self.assertEqual(sum(1 for m in a.values() if m == "m1"), 8)

    def test_rules_only_baseline_on_fixtures(self):
        import os
        from unittest import mock
        from evals.run_comparison import FIXTURE_DIR, load_cases, rules_only_rows
        from src.local_services import start_vendor_api_in_background
        with mock.patch.dict(os.environ, {"PROCUREMENT_DATA_DIR": str(FIXTURE_DIR),
                                          "VENDOR_RISK_BASE_URL": start_vendor_api_in_background()}):
            rows = {r["case_id"]: r for r in rules_only_rows(load_cases("full"))}
        self.assertEqual(len(rows), 23)
        # The rules alone cannot judge a product-tier duplicate (needs the justification): the model's job.
        self.assertFalse(rows["DS-1008"]["correct_next_action"])
        others = [cid for cid, r in rows.items() if cid != "DS-1008"]
        self.assertTrue(all(rows[c]["correct_next_action"] and rows[c]["policy_followed"] for c in others),
                        [(c, rows[c]["problems"]) for c in others if rows[c]["problems"]])


class RescoreTests(unittest.TestCase):
    def test_gold_revision_applies_to_saved_runs(self):
        from evals.run_comparison import rescore
        saved = {"decision": {"recommendation": "Use existing tool / confirm need: spare seats", "required_approvals": ["Manager"],
                              "risk_flags": [], "missing_information": [], "human_review_required": True},
                 "draft": None, "problems": ["label Use existing tool / confirm need not in ['Proceed to approval routing']"],
                 "correct_next_action": False}
        case = {"expect": {"labels": ["Proceed to approval routing", "Use existing tool / confirm need"],
                           "approvals_required": ["Manager"], "approvals_forbidden": ["CFO"]}}
        updated = rescore(saved, case)
        self.assertTrue(updated["correct_next_action"])
        self.assertEqual(updated["problems"], [])
