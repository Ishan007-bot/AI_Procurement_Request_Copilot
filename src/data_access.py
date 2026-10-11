"""Data-access helpers over the procurement snapshot.

CSV cells are read as strings with blanks kept as "" (pandas would otherwise turn
empty dates and manager IDs into NaN, which is truthy and breaks checks such as
`if row["security_review_date"]`). Numbers are converted explicitly.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from src.config import get_data_dir

ROOT = Path(__file__).resolve().parents[1]

APPROVER_LEVELS = {"director", "vp"}


def _read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(get_data_dir() / name, dtype=str, keep_default_na=False)


def load_employees() -> pd.DataFrame:
    return _read_csv("employees.csv")


def load_budgets() -> pd.DataFrame:
    return _read_csv("department_budgets.csv")


def load_software_catalog() -> pd.DataFrame:
    return _read_csv("software_catalog.csv")


def load_vendors() -> pd.DataFrame:
    return _read_csv("vendors.csv")


def load_purchase_history() -> pd.DataFrame:
    return _read_csv("purchase_history.csv")


def load_requests() -> list[dict]:
    return json.loads((get_data_dir() / "requests.json").read_text(encoding="utf-8"))


def get_request(request_id: str) -> dict:
    for request in load_requests():
        if request["request_id"] == request_id:
            return request
    raise KeyError(f"Unknown request_id: {request_id}")


def load_policy_text() -> str:
    return (get_data_dir() / "procurement_policy.md").read_text(encoding="utf-8")


def to_number(value: object) -> float | None:
    """Parse a money/count value; None for blanks, nulls, NaN and non-numeric text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) else float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text.lower() in {"nan", "null", "none", "n/a"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return None if math.isnan(number) else number


def _records(df: pd.DataFrame) -> list[dict]:
    return df.to_dict(orient="records")


def _norm(value: object) -> str:
    return str(value or "").strip().casefold()


def get_employee(employee_id: str) -> dict | None:
    target = _norm(employee_id)
    return next((r for r in _records(load_employees()) if _norm(r["employee_id"]) == target), None)


def get_department_budget(department: str) -> dict | None:
    target = _norm(department)
    for row in _records(load_budgets()):
        if _norm(row["department"]) == target:
            return {
                "department": row["department"],
                "annual_software_budget_usd": to_number(row["annual_software_budget_usd"]),
                "committed_usd": to_number(row["committed_usd"]),
                "available_usd": to_number(row["available_usd"]),
            }
    return None


def get_vendor_record(vendor_name: str) -> dict | None:
    target = _norm(vendor_name)
    return next((r for r in _records(load_vendors()) if _norm(r["vendor_name"]) == target), None)


def get_reporting_chain(employee_id: str) -> list[dict]:
    """Managers above the employee, nearest first. Stops on unknown IDs or cycles."""
    by_id = {r["employee_id"]: r for r in _records(load_employees())}
    chain: list[dict] = []
    seen = {employee_id}
    current = by_id.get(employee_id)
    while current and current["manager_id"]:
        manager_id = current["manager_id"]
        if manager_id in seen or manager_id not in by_id:
            break
        seen.add(manager_id)
        current = by_id[manager_id]
        chain.append(current)
    return chain


def _person(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in ("employee_id", "name", "department", "level")}


def resolve_approvers(employee_id: str) -> dict:
    """Resolve the named Manager and Department Head for a requester.

    Rule (see docs/assumptions.md): Manager = direct manager. Department Head =
    the first Director/VP strictly above the requester, so nobody approves their
    own request. The org data has a "Go To Market" director managing Sales and
    Customer Success, which has no budget row, so a departmental lookup by name
    alone would fail; walking the reporting chain resolves it.
    """
    requester = get_employee(employee_id)
    notes: list[str] = []
    if requester is None:
        return {"requester": None, "manager": None, "department_head": None, "notes": [f"Unknown requester '{employee_id}'"]}

    chain = get_reporting_chain(requester["employee_id"])
    manager = chain[0] if chain else None
    department_head = next((p for p in chain if _norm(p["level"]) in APPROVER_LEVELS), None)

    if manager is None:
        notes.append("Requester has no manager on record; Manager approval must be assigned by Procurement")
    if department_head is None:
        notes.append("No Director/VP above the requester; Department Head approval must be assigned by Procurement")
    elif _norm(department_head["department"]) != _norm(requester["department"]):
        notes.append(
            f"Department Head resolved via reporting line: {department_head['name']} "
            f"({department_head['level']}, {department_head['department']}) for a {requester['department']} requester"
        )
    return {
        "requester": _person(requester),
        "manager": _person(manager),
        "department_head": _person(department_head),
        "notes": notes,
    }
