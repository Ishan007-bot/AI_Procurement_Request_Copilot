from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from src import config, data_access as da


class NullHandlingTests(unittest.TestCase):
    def test_blank_csv_cells_are_empty_strings(self):
        brandboard = da.get_vendor_record("BrandBoard")
        self.assertEqual(brandboard["security_review_date"], "")
        self.assertEqual(da.get_employee("E010")["manager_id"], "")

    def test_to_number(self):
        self.assertEqual(da.to_number(800), 800.0)
        self.assertEqual(da.to_number("12,000"), 12000.0)
        self.assertEqual(da.to_number("$950.50"), 950.5)
        for blank in (None, "", "null", "NaN", float("nan"), "abc", True):
            self.assertIsNone(da.to_number(blank), blank)

    def test_budget_is_numeric(self):
        budget = da.get_department_budget("sales")
        self.assertEqual(budget["available_usd"], 18000.0)
        self.assertIsNone(da.get_department_budget("Go To Market"))

    def test_vendor_lookup_is_case_insensitive(self):
        self.assertEqual(da.get_vendor_record("signalwatch")["vendor_id"], "V005")
        self.assertIsNone(da.get_vendor_record("Unknown Vendor"))


class ApproverResolutionTests(unittest.TestCase):
    def test_ic_reporting_to_director(self):
        approvers = da.resolve_approvers("E004")
        self.assertEqual(approvers["manager"]["name"], "Priya Shah")
        self.assertEqual(approvers["department_head"]["name"], "Priya Shah")
        self.assertEqual(approvers["notes"], [])

    def test_department_head_via_go_to_market(self):
        approvers = da.resolve_approvers("E003")  # Sales IC -> Go To Market director
        self.assertEqual(approvers["department_head"]["employee_id"], "E007")
        self.assertTrue(any("reporting line" in n for n in approvers["notes"]))

    def test_director_does_not_approve_own_request(self):
        approvers = da.resolve_approvers("E009")
        self.assertEqual(approvers["department_head"]["employee_id"], "E010")

    def test_top_of_org_has_no_approvers(self):
        approvers = da.resolve_approvers("E010")
        self.assertIsNone(approvers["manager"])
        self.assertIsNone(approvers["department_head"])
        self.assertEqual(len(approvers["notes"]), 2)

    def test_unknown_requester(self):
        approvers = da.resolve_approvers("E999")
        self.assertIsNone(approvers["requester"])
        self.assertIn("Unknown requester", approvers["notes"][0])


class ConfigurableDataDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(config.DEFAULT_DATA_DIR, self.tmp / "data")
        self.data = self.tmp / "data"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_overlay_dir_is_used(self):
        csv = self.data / "department_budgets.csv"
        csv.write_text(csv.read_text(encoding="utf-8").replace("Sales,220000,202000,18000", "Sales,220000,200000,20000"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"PROCUREMENT_DATA_DIR": str(self.data)}):
            self.assertEqual(da.get_department_budget("Sales")["available_usd"], 20000.0)
        self.assertEqual(da.get_department_budget("Sales")["available_usd"], 18000.0)

    def test_reference_date_follows_policy_file(self):
        policy = self.data / "procurement_policy.md"
        policy.write_text(policy.read_text(encoding="utf-8").replace("2026-09-30", "2027-01-15"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"PROCUREMENT_DATA_DIR": str(self.data)}):
            self.assertEqual(config.get_reference_date(), date(2027, 1, 15))
        self.assertEqual(config.get_reference_date(), date(2026, 9, 30))


if __name__ == "__main__":
    unittest.main()
