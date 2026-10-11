"""Collects everything the policy engine needs for one request (the I/O side)."""
from __future__ import annotations

from src import data_access as da
from src.config import get_reference_date
from src.policy_engine import PolicyInputs, PolicyResult, evaluate
from src.vendor_client import VendorRiskResult, fetch_vendor_risk


def collect_policy_inputs(request: dict, vendor_risk: VendorRiskResult | None = None) -> PolicyInputs:
    approvers = da.resolve_approvers(str(request.get("requester_id") or ""))
    department = (approvers.get("requester") or {}).get("department")
    risk = vendor_risk if vendor_risk is not None else fetch_vendor_risk(str(request.get("vendor_name") or ""))
    return PolicyInputs(
        request=request,
        approvers=approvers,
        budget=da.get_department_budget(department) if department else None,
        catalog=da.load_software_catalog().to_dict(orient="records"),
        purchase_history=da.load_purchase_history().to_dict(orient="records"),
        vendor_record=da.get_vendor_record(str(request.get("vendor_name") or "")),
        vendor_risk=risk.to_dict(),
        reference_date=get_reference_date(),
    )


def evaluate_request(request: dict, vendor_risk: VendorRiskResult | None = None) -> PolicyResult:
    return evaluate(collect_policy_inputs(request, vendor_risk))
