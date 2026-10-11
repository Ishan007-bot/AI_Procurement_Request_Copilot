from __future__ import annotations

import os
import unittest
from datetime import date, timedelta

from src import policy_engine as pe
from src.policy_engine import PolicyInputs, evaluate

REF = date(2026, 9, 30)


def make_inputs(request: dict | None = None, *, vendor_record: dict | None | str = "default",
                vendor_risk: dict | None = None, budget: dict | None | str = "default",
                catalog: list[dict] | None = None, approvers: dict | None = None) -> PolicyInputs:
    """A clean baseline: known requester, approved current vendor, ample budget, no overlap."""
    req = {
        "request_id": "REQ-T",
        "requester_id": "E100",
        "product_name": "Widget Pro",
        "vendor_name": "WidgetCo",
        "category": "Widgets",
        "annual_cost_usd": 500,
        "user_count": 5,
        "business_justification": "Team needs widgets for the quarterly planning process.",
        "data_access_level": "internal_documents",
        "requested_integrations": [],
        "urgency": "normal",
    }
    req.update(request or {})
    review = (REF - timedelta(days=100)).isoformat()
    return PolicyInputs(
        request=req,
        approvers=approvers or {
            "requester": {"employee_id": "E100", "name": "Test User", "department": "Ops", "level": "IC3"},
            "manager": {"employee_id": "E200", "name": "Mgr", "department": "Ops", "level": "Director"},
            "department_head": {"employee_id": "E200", "name": "Mgr", "department": "Ops", "level": "Director"},
            "notes": [],
        },
        budget={"department": "Ops", "annual_software_budget_usd": 100000.0, "committed_usd": 50000.0, "available_usd": 50000.0}
        if budget == "default" else budget,
        catalog=catalog or [],
        purchase_history=[],
        vendor_record={
            "vendor_id": "V900", "vendor_name": "WidgetCo", "procurement_status": "Approved", "security_status": "Approved",
            "security_review_date": review, "legal_terms_status": "Approved", "notes": "Standard vendor",
        } if vendor_record == "default" else vendor_record,
        vendor_risk=vendor_risk or {
            "status": "ok", "data": {
                "risk_level": "low", "security_review_status": "approved", "last_review_date": review,
                "processes_personal_data": False, "stores_data_outside_region": False, "notes": "Current assessment.",
            },
        },
        reference_date=REF,
    )


def api(**overrides) -> dict:
    data = {"risk_level": "low", "security_review_status": "approved", "last_review_date": (REF - timedelta(days=100)).isoformat(),
            "processes_personal_data": False, "stores_data_outside_region": False, "notes": "Current assessment."}
    data.update(overrides)
    return {"status": "ok", "data": data}


class BaselineTests(unittest.TestCase):
    def test_clean_request_is_manager_only_and_proceeds(self):
        r = evaluate(make_inputs())
        self.assertEqual(r.required_approvals, ["Manager"])
        self.assertEqual(r.risk_flags, [])
        self.assertEqual(r.missing_information, [])
        self.assertEqual(r.baseline_recommendation, pe.PROCEED)
        self.assertTrue(r.human_review_required)
        self.assertTrue(r.vendor_assessment["assessment_current"])
        self.assertEqual(r.approver_assignments["Manager"], "Mgr (E200)")


class ThresholdTests(unittest.TestCase):
    def approvals(self, cost):
        return evaluate(make_inputs({"annual_cost_usd": cost})).required_approvals

    def test_boundaries(self):
        self.assertEqual(self.approvals(1000), ["Manager"])
        self.assertEqual(self.approvals(1000.01), ["Department Head", "Procurement"])
        self.assertEqual(self.approvals(10000), ["Department Head", "Procurement"])
        self.assertEqual(self.approvals(10000.01), ["Department Head", "Procurement", "Finance"])
        self.assertEqual(self.approvals(25000), ["Department Head", "Procurement", "Finance"])
        self.assertEqual(self.approvals(25000.01), ["Department Head", "Procurement", "Finance", "CFO"])
        self.assertEqual(self.approvals(0), ["Manager"])

    def test_string_cost_is_parsed(self):
        self.assertIn("CFO", self.approvals("30,000"))

    def test_missing_cost_cannot_compute_tier(self):
        r = evaluate(make_inputs({"annual_cost_usd": None}))
        self.assertIsNone(r.threshold_tier)
        self.assertIn("Procurement", r.required_approvals)
        self.assertNotIn("Manager", r.required_approvals)
        self.assertIn(pe.MISSING_COST, r.missing_information)
        self.assertEqual(r.baseline_recommendation, pe.CLARIFY)


class BudgetTests(unittest.TestCase):
    def budget(self, available):
        return {"department": "Ops", "annual_software_budget_usd": 100000.0, "committed_usd": 100000.0 - available, "available_usd": available}

    def test_equal_to_available_is_within(self):
        r = evaluate(make_inputs({"annual_cost_usd": 5000}, budget=self.budget(5000)))
        self.assertEqual(r.budget_check["status"], "within_budget")
        self.assertEqual(r.budget_check["remaining_after_usd"], 0)
        self.assertNotIn("budget_insufficient", r.risk_flags)

    def test_over_budget_flags_and_routes_to_finance(self):
        r = evaluate(make_inputs({"annual_cost_usd": 5000.01}, budget=self.budget(5000)))
        self.assertIn("budget_insufficient", r.risk_flags)
        self.assertIn("Finance", r.required_approvals)
        self.assertEqual(r.budget_check["shortfall_usd"], 0.01)
        self.assertEqual(r.baseline_recommendation, pe.BUDGET_EXCEPTION)

    def test_missing_budget_record(self):
        r = evaluate(make_inputs(budget=None))
        self.assertIn("budget_record_missing", r.risk_flags)
        self.assertIn("Finance", r.required_approvals)
        self.assertEqual(r.baseline_recommendation, pe.MANUAL_REVIEW)
        self.assertIn("Finance to verify", r.baseline_next_step)


class RequiredInformationTests(unittest.TestCase):
    def test_nulls_and_unknowns(self):
        r = evaluate(make_inputs({"user_count": None, "data_access_level": "unknown", "requested_integrations": None,
                                  "business_justification": "  "}))
        for item in (pe.MISSING_USERS, pe.MISSING_DATA_LEVEL, pe.MISSING_INTEGRATIONS, pe.MISSING_PURPOSE):
            self.assertIn(item, r.missing_information)
        self.assertIn("missing_information", r.risk_flags)
        self.assertIn("Security", r.required_approvals)  # unknown data level: cannot rule out sensitive access

    def test_empty_integration_list_means_none(self):
        self.assertEqual(evaluate(make_inputs({"requested_integrations": []})).missing_information, [])

    def test_data_level_none_is_valid(self):
        r = evaluate(make_inputs({"data_access_level": "none"}))
        self.assertEqual(r.missing_information, [])
        self.assertNotIn("Security", r.required_approvals)

    def test_zero_users_is_missing(self):
        self.assertIn(pe.MISSING_USERS, evaluate(make_inputs({"user_count": 0})).missing_information)

    def test_unknown_requester(self):
        r = evaluate(make_inputs(approvers={"requester": None, "manager": None, "department_head": None, "notes": ["Unknown requester 'X'"]}))
        self.assertIn(pe.MISSING_REQUESTER, r.missing_information)
        self.assertEqual(r.approver_assignments["Manager"], "To be assigned by Procurement")


class SecurityTriggerTests(unittest.TestCase):
    def test_sensitive_data_levels(self):
        for level in ("source_code", "confidential_documents", "employee_pii", "customer_pii", "credentials", "production_telemetry"):
            r = evaluate(make_inputs({"data_access_level": level}))
            self.assertIn("Security", r.required_approvals, level)
            self.assertIn("security_review_required", r.risk_flags, level)

    def test_non_sensitive_levels(self):
        for level in ("internal_documents", "internal_marketing", "none", "public"):
            self.assertNotIn("Security", evaluate(make_inputs({"data_access_level": level})).required_approvals, level)

    def test_integration_triggers(self):
        for integration in ("Production cloud account", "AWS", "Git repositories", "GitHub", "Secrets vault"):
            r = evaluate(make_inputs({"requested_integrations": [integration]}))
            self.assertIn("Security", r.required_approvals, integration)
        for integration in ("SSO", "Slack", "HelpDeskly", "Digital signage"):
            self.assertNotIn("Security", evaluate(make_inputs({"requested_integrations": [integration]})).required_approvals, integration)


class VendorAssessmentTests(unittest.TestCase):
    def record(self, **overrides):
        base = {"vendor_id": "V900", "vendor_name": "WidgetCo", "procurement_status": "Approved", "security_status": "Approved",
                "security_review_date": (REF - timedelta(days=100)).isoformat(), "legal_terms_status": "Approved", "notes": ""}
        base.update(overrides)
        return base

    def test_365_days_is_current_366_is_expired(self):
        d365, d366 = (REF - timedelta(days=365)).isoformat(), (REF - timedelta(days=366)).isoformat()
        current = evaluate(make_inputs(vendor_record=self.record(security_review_date=d365), vendor_risk=api(last_review_date=d365)))
        self.assertNotIn("vendor_review_expired", current.risk_flags)
        self.assertNotIn("Security", current.required_approvals)
        expired = evaluate(make_inputs(vendor_record=self.record(security_review_date=d366), vendor_risk=api(last_review_date=d366)))
        self.assertIn("vendor_review_expired", expired.risk_flags)
        self.assertIn("Security", expired.required_approvals)

    def test_registry_vs_api_status_conflict(self):
        r = evaluate(make_inputs(vendor_risk=api(security_review_status="expired")))
        self.assertIn("conflicting_vendor_evidence", r.risk_flags)
        self.assertEqual(r.baseline_recommendation, pe.MANUAL_REVIEW)
        self.assertTrue(any("Neither source is assumed correct" in f.finding for f in r.findings))

    def test_date_conflict(self):
        r = evaluate(make_inputs(vendor_risk=api(last_review_date=(REF - timedelta(days=50)).isoformat())))
        self.assertIn("conflicting_vendor_evidence", r.risk_flags)

    def test_pending_vs_not_completed_is_consistent_not_conflict(self):
        r = evaluate(make_inputs(vendor_record=self.record(security_status="Pending", security_review_date=""),
                                 vendor_risk=api(security_review_status="not_completed", last_review_date=None)))
        self.assertNotIn("conflicting_vendor_evidence", r.risk_flags)
        self.assertIn("Security", r.required_approvals)

    def test_api_unavailable_is_never_favourable(self):
        r = evaluate(make_inputs({"data_access_level": "confidential_documents"},
                                 vendor_risk={"status": "unavailable", "data": None, "error_detail": "503"}))
        self.assertIn("vendor_risk_unavailable", r.risk_flags)
        self.assertIn("Security", r.required_approvals)
        self.assertIn("Privacy", r.required_approvals)  # residency unverifiable for sensitive data
        self.assertFalse(r.vendor_assessment["assessment_current"])
        self.assertEqual(r.baseline_recommendation, pe.MANUAL_REVIEW)

    def test_api_record_missing(self):
        r = evaluate(make_inputs(vendor_risk={"status": "not_found", "data": None, "error_detail": "404"}))
        self.assertIn("vendor_risk_record_missing", r.risk_flags)
        self.assertIn("Security", r.required_approvals)

    def test_vendor_not_in_registry(self):
        r = evaluate(make_inputs(vendor_record=None))
        self.assertIn("vendor_not_in_registry", r.risk_flags)
        self.assertIn("Legal", r.required_approvals)  # terms unknown
        self.assertIn("Procurement", r.required_approvals)

    def test_rejected_vendor_do_not_proceed(self):
        r = evaluate(make_inputs(vendor_record=self.record(procurement_status="Rejected")))
        self.assertIn("vendor_blocked", r.risk_flags)
        self.assertEqual(r.baseline_recommendation, pe.DO_NOT_PROCEED)

    def test_future_review_date_is_not_trusted(self):
        future = (REF + timedelta(days=10)).isoformat()
        r = evaluate(make_inputs(vendor_record=self.record(security_review_date=future), vendor_risk=api(last_review_date=future)))
        self.assertIn("Security", r.required_approvals)
        self.assertFalse(r.vendor_assessment["assessment_current"])

    def test_high_risk_vendor(self):
        r = evaluate(make_inputs(vendor_risk=api(risk_level="high")))
        self.assertIn("high_vendor_risk", r.risk_flags)
        self.assertIn("Security", r.required_approvals)


class PrivacyLegalTests(unittest.TestCase):
    def new_vendor(self, terms="Approved"):
        return {"vendor_id": "V901", "vendor_name": "WidgetCo", "procurement_status": "New", "security_status": "Approved",
                "security_review_date": (REF - timedelta(days=100)).isoformat(), "legal_terms_status": terms, "notes": ""}

    def test_new_vendor_legal_boundary_is_inclusive(self):
        at = evaluate(make_inputs({"annual_cost_usd": 10000}, vendor_record=self.new_vendor()))
        self.assertIn("Legal", at.required_approvals)
        self.assertNotIn("Finance", at.required_approvals)  # Finance starts above $10,000
        below = evaluate(make_inputs({"annual_cost_usd": 9999.99}, vendor_record=self.new_vendor()))
        self.assertNotIn("Legal", below.required_approvals)

    def test_new_vendor_unknown_cost_needs_legal(self):
        self.assertIn("Legal", evaluate(make_inputs({"annual_cost_usd": None}, vendor_record=self.new_vendor())).required_approvals)

    def test_unapproved_terms_need_legal(self):
        for terms in ("Draft", "Unknown", ""):
            self.assertIn("Legal", evaluate(make_inputs(vendor_record=self.new_vendor(terms))).required_approvals, terms)

    def test_pii_needs_privacy(self):
        for level in ("employee_pii", "customer_pii"):
            r = evaluate(make_inputs({"data_access_level": level}))
            self.assertIn("Privacy", r.required_approvals)
            self.assertIn("privacy_review_required", r.risk_flags)

    def test_cross_region_sensitive_needs_privacy_and_legal(self):
        r = evaluate(make_inputs({"data_access_level": "confidential_documents"}, vendor_risk=api(stores_data_outside_region=True)))
        self.assertIn("Privacy", r.required_approvals)
        self.assertIn("Legal", r.required_approvals)

    def test_cross_region_non_sensitive_is_not_triggered(self):
        r = evaluate(make_inputs({"data_access_level": "internal_marketing"}, vendor_risk=api(stores_data_outside_region=True)))
        self.assertNotIn("Privacy", r.required_approvals)


class OverlapAndRestrictionTests(unittest.TestCase):
    def catalog_row(self, **overrides):
        row = {"software_id": "SW900", "product_name": "Widget", "category": "Widgets", "vendor_name": "WidgetCo",
               "status": "Approved", "annual_cost_usd": "1000", "licensed_seats": "100", "scope": "Company-wide", "notes": ""}
        row.update(overrides)
        return row

    def test_product_family_is_expansion(self):
        r = evaluate(make_inputs(catalog=[self.catalog_row()]))
        self.assertIn("existing_tool_overlap", r.risk_flags)
        self.assertEqual(r.overlap_candidates[0]["relationship"], "same product / expansion")
        self.assertEqual(r.baseline_recommendation, pe.PROCEED)

    def test_category_alternative_suggests_existing_tool(self):
        r = evaluate(make_inputs(catalog=[self.catalog_row(product_name="Gadget", vendor_name="GadgetCo")]))
        self.assertEqual(r.overlap_candidates[0]["relationship"], "alternative")
        self.assertEqual(r.baseline_recommendation, pe.USE_EXISTING)

    def test_other_department_scope_not_available(self):
        r = evaluate(make_inputs(catalog=[self.catalog_row(product_name="Gadget", vendor_name="GadgetCo", scope="Engineering")]))
        self.assertFalse(r.overlap_candidates[0]["available_to_requester"])
        self.assertEqual(r.baseline_recommendation, pe.PROCEED)

    def test_no_overlap(self):
        r = evaluate(make_inputs(catalog=[self.catalog_row(product_name="Other", vendor_name="OtherCo", category="Other")]))
        self.assertNotIn("existing_tool_overlap", r.risk_flags)

    def test_word_boundary_matching(self):
        r = evaluate(make_inputs({"product_name": "Widgetry"}, catalog=[self.catalog_row(vendor_name="X", category="Y")]))
        self.assertEqual(r.overlap_candidates, [])

    def test_limited_use_approval_does_not_cover_pii(self):
        row = self.catalog_row(status="Approved - limited use", notes="sensitive data restrictions apply")
        r = evaluate(make_inputs({"data_access_level": "customer_pii"}, catalog=[row]))
        self.assertIn("ai_use_case_not_covered", r.risk_flags)
        self.assertIn("Security", r.required_approvals)
        self.assertIn("Privacy", r.required_approvals)

    def test_limited_use_with_low_risk_data_is_fine(self):
        row = self.catalog_row(status="Approved - limited use")
        r = evaluate(make_inputs({"data_access_level": "internal_documents"}, catalog=[row]))
        self.assertNotIn("ai_use_case_not_covered", r.risk_flags)


class DatasetFloorTests(unittest.TestCase):
    """The engine alone over the provided snapshot (plan.md Appendix A)."""

    EXPECTED = {
        "REQ-1001": (["Manager"], {"existing_tool_overlap"}, pe.PROCEED),
        "REQ-1002": (["Department Head", "Procurement", "Finance", "Security", "Legal"],
                     {"existing_tool_overlap", "security_review_required", "legal_review_required"}, pe.SPECIALIST),
        "REQ-1003": (["Department Head", "Procurement", "Finance", "Security"], {"security_review_required"}, pe.SPECIALIST),
        "REQ-1004": (["Department Head", "Procurement", "Security", "Privacy", "Legal"],
                     {"ai_use_case_not_covered", "privacy_review_required", "legal_review_required"}, pe.SPECIALIST),
        "REQ-1005": (["Department Head", "Procurement", "Finance", "Security", "Privacy", "Legal"],
                     {"budget_insufficient", "security_review_required", "privacy_review_required", "legal_review_required"}, pe.BUDGET_EXCEPTION),
        "REQ-1006": (["Procurement", "Security"], {"missing_information", "prompt_injection_detected", "unverified_approval_claim"}, pe.CLARIFY),
        "REQ-1007": (["Department Head", "Procurement", "Finance", "Security"],
                     {"vendor_review_expired", "conflicting_vendor_evidence"}, pe.MANUAL_REVIEW),
        "REQ-1008": (["Department Head", "Procurement"], {"existing_tool_overlap"}, pe.PROCEED),
        "REQ-1009": (["Department Head", "Procurement", "Finance", "Security", "Privacy", "Legal"],
                     {"vendor_risk_unavailable", "legal_review_required"}, pe.MANUAL_REVIEW),
        "REQ-1010": (["Manager"], set(), pe.PROCEED),
    }

    @classmethod
    def setUpClass(cls):
        from src.local_services import start_vendor_api_in_background
        cls._previous = os.environ.get("VENDOR_RISK_BASE_URL")
        os.environ["VENDOR_RISK_BASE_URL"] = start_vendor_api_in_background()

    @classmethod
    def tearDownClass(cls):
        if cls._previous is None:
            os.environ.pop("VENDOR_RISK_BASE_URL", None)
        else:
            os.environ["VENDOR_RISK_BASE_URL"] = cls._previous

    def test_dataset_floor(self):
        from src.data_access import get_request
        from src.evidence import evaluate_request
        for request_id, (approvals, flags, label) in self.EXPECTED.items():
            with self.subTest(request_id):
                r = evaluate_request(get_request(request_id))
                self.assertEqual(r.required_approvals, approvals)
                self.assertTrue(flags <= set(r.risk_flags), f"missing {flags - set(r.risk_flags)}")
                self.assertEqual(r.baseline_recommendation, label)
                self.assertTrue(r.human_review_required)
                self.assertNotIn("CFO", r.required_approvals)

    def test_public_eval_floor_expectations(self):
        """Engine floor alone satisfies the approval/flag/missing-info parts of the public cases."""
        import json
        from src.config import ROOT
        from src.data_access import get_request
        from src.evidence import evaluate_request
        cases = json.loads((ROOT / "evals" / "public_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case["case_id"]):
                r = evaluate_request(get_request(case["request_id"]))
                exp = case["expectations"]
                text = lambda items: [str(i).lower() for i in items]
                for group in exp.get("required_approvals_any_groups", []):
                    self.assertTrue(any(t in a for t in group for a in text(r.required_approvals)), group)
                for group in exp.get("missing_information_any_groups", []):
                    self.assertTrue(any(t in m for t in group for m in text(r.missing_information)), group)
                for token in exp.get("risk_flags_must_not_contain", []):
                    self.assertFalse(any(token in f for f in text(r.risk_flags)), token)
                for group in exp.get("risk_flags_any_groups", []):
                    self.assertTrue(any(t in f for t in group for f in text(r.risk_flags)), group)
                if "max_missing_information" in exp:
                    self.assertLessEqual(len(r.missing_information), exp["max_missing_information"])


if __name__ == "__main__":
    unittest.main()
