from __future__ import annotations

import os
import unittest
from unittest import mock

import requests
from src.local_services import start_vendor_api_in_background
from src.vendor_client import fetch_vendor_risk


class VendorClientOverHttpTests(unittest.TestCase):
    """Runs against the real mock API over HTTP on a free port."""

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

    def test_known_vendor_ok(self):
        result = fetch_vendor_risk("SignalWatch")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data["security_review_status"], "expired")
        self.assertEqual(result.attempts, 1)

    def test_name_with_space(self):
        self.assertEqual(fetch_vendor_risk("CreativeSuite Labs").status, "ok")

    def test_unknown_vendor_not_found(self):
        result = fetch_vendor_risk("NoSuchVendor")
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.http_status, 404)
        self.assertIn("NoSuchVendor", result.error_detail)

    def test_forced_outage_unavailable_after_retry(self):
        result = fetch_vendor_risk("NimbusAI")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.http_status, 503)
        self.assertEqual(result.attempts, 2)
        self.assertIn("temporarily unavailable", result.error_detail)
        self.assertIsNone(result.data)

    # Names are percent-encoded by the client and decoded exactly once by the API
    # (the starter API decoded twice and 404'd names containing "/" at the router).
    def test_percent_sign_in_name_round_trips(self):
        result = fetch_vendor_risk("Acme%Co")
        self.assertEqual(result.status, "not_found")
        self.assertIn("'Acme%Co'", result.error_detail)

    def test_slash_in_name_reaches_handler(self):
        result = fetch_vendor_risk("A/B Labs")
        self.assertEqual(result.status, "not_found")
        self.assertIn("'A/B Labs'", result.error_detail)

    def test_empty_name_is_error_without_call(self):
        result = fetch_vendor_risk("  ")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.attempts, 0)


class VendorClientFailureModeTests(unittest.TestCase):
    def test_connection_refused_is_unavailable(self):
        with mock.patch.dict(os.environ, {"VENDOR_RISK_BASE_URL": "http://127.0.0.1:9"}):
            with mock.patch("src.vendor_client.time.sleep"):
                result = fetch_vendor_risk("PixelCraft", timeout_seconds=0.5)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.http_status)
        self.assertEqual(result.attempts, 2)

    def test_timeout_is_unavailable(self):
        with mock.patch("src.vendor_client.requests.get", side_effect=requests.Timeout()):
            with mock.patch("src.vendor_client.time.sleep"):
                result = fetch_vendor_risk("PixelCraft", timeout_seconds=0.1)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("Timed out", result.error_detail)

    def test_non_json_body_is_error(self):
        response = mock.Mock(status_code=200)
        response.json.side_effect = ValueError("not json")
        with mock.patch("src.vendor_client.requests.get", return_value=response):
            result = fetch_vendor_risk("PixelCraft")
        self.assertEqual(result.status, "error")
        self.assertIsNone(result.data)

    def test_recovers_when_retry_succeeds(self):
        good = mock.Mock(status_code=200)
        good.json.return_value = {"vendor_name": "PixelCraft", "security_review_status": "approved"}
        with mock.patch("src.vendor_client.requests.get", side_effect=[requests.ConnectionError(), good]):
            with mock.patch("src.vendor_client.time.sleep"):
                result = fetch_vendor_risk("PixelCraft")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.notes), 1)


if __name__ == "__main__":
    unittest.main()
