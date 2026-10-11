"""Append-only log of HUMAN decisions taken in the UI.

The copilot never writes here on its own: a record is created only when a
reviewer clicks a decision button. Stored as JSON Lines under logs/ (git-ignored).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import ROOT

LOG_PATH = ROOT / "logs" / "decisions.jsonl"
HUMAN_ACTIONS = ["Route for approval", "Request more information", "Reject request", "Escalate exception"]


def append_decision(record: dict, path: Path = LOG_PATH) -> dict:
    if record.get("action") not in HUMAN_ACTIONS:
        raise ValueError(f"action must be one of {HUMAN_ACTIONS}")
    if not str(record.get("reviewer") or "").strip():
        raise ValueError("reviewer is required")
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decided_by": "human", **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_decisions(request_id: str | None = None, path: Path = LOG_PATH) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if request_id is None or entry.get("request_id") == request_id:
            entries.append(entry)
    return entries
