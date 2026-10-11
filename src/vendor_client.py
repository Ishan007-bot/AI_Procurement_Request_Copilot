"""Client for the external vendor-risk service.

Returns a typed result instead of raising, because policy section 10 treats
"record missing", "service unavailable" and "bad response" differently from a
successful lookup, and none of them may be read as a favourable status.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Literal
from urllib.parse import quote

import requests

from src.config import get_vendor_risk_base_url, get_vendor_risk_timeout

VendorRiskStatus = Literal["ok", "not_found", "unavailable", "error"]

RETRY_BACKOFF_SECONDS = 0.25


@dataclass
class VendorRiskResult:
    vendor_name: str
    status: VendorRiskStatus
    data: dict | None = None
    http_status: int | None = None
    error_detail: str | None = None
    endpoint: str | None = None
    attempts: int = 0
    latency_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return asdict(self)


def _detail(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)[:200]


def fetch_vendor_risk(
    vendor_name: str,
    timeout_seconds: float | None = None,
    retries: int = 1,
) -> VendorRiskResult:
    """Look up a vendor. Retries once on connection errors, timeouts and 5xx."""
    name = (vendor_name or "").strip()
    if not name:
        return VendorRiskResult(vendor_name=vendor_name or "", status="error", error_detail="No vendor name supplied")

    timeout = timeout_seconds if timeout_seconds is not None else get_vendor_risk_timeout()
    endpoint = f"{get_vendor_risk_base_url()}/vendor-risk/{quote(name, safe='')}"
    result = VendorRiskResult(vendor_name=name, status="error", endpoint=endpoint)
    started = time.perf_counter()

    for attempt in range(1, retries + 2):
        result.attempts = attempt
        try:
            response = requests.get(endpoint, timeout=timeout)
        except requests.Timeout:
            result.status, result.http_status = "unavailable", None
            result.error_detail = f"Timed out after {timeout:.1f}s"
        except requests.ConnectionError as exc:
            result.status, result.http_status = "unavailable", None
            result.error_detail = f"Could not connect to vendor-risk service: {type(exc).__name__}"
        else:
            result.http_status = response.status_code
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    result.status, result.data, result.error_detail = "ok", payload, None
                else:
                    result.status, result.error_detail = "error", "Vendor-risk service returned a non-JSON or non-object body"
                break
            if response.status_code == 404:
                result.status, result.error_detail = "not_found", _detail(response)
                break
            if response.status_code >= 500:
                result.status, result.error_detail = "unavailable", _detail(response)
            else:
                result.status, result.error_detail = "error", _detail(response)
                break
        if attempt <= retries:
            result.notes.append(f"attempt {attempt} failed ({result.error_detail}); retrying")
            time.sleep(RETRY_BACKOFF_SECONDS)

    result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return result


def get_vendor_risk(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    """Starter-compatible helper: returns the record or raises on any failure."""
    result = fetch_vendor_risk(vendor_name, timeout_seconds=timeout_seconds, retries=0)
    if not result.ok:
        raise RuntimeError(f"Vendor-risk lookup failed ({result.status}): {result.error_detail}")
    return result.data
