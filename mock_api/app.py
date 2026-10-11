from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException

from src.config import get_data_dir

app = FastAPI(title="FDE Mock Vendor Risk API", version="1.1")


def _load_risk_data() -> dict:
    # Read per request (the file is tiny) so PROCUREMENT_DATA_DIR overlays are honoured.
    return json.loads((get_data_dir() / "vendor_risk.json").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# `:path` lets vendor names containing "/" reach this handler instead of the router's generic 404.
# FastAPI has already percent-decoded the value, so it must not be unquoted again.
@app.get("/vendor-risk/{vendor_name:path}")
def vendor_risk(vendor_name: str) -> dict:
    record = _load_risk_data().get(vendor_name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No vendor-risk record for '{vendor_name}'")
    if record.get("force_error"):
        raise HTTPException(status_code=503, detail=record.get("error_message", "Vendor-risk service unavailable"))
    return {"vendor_name": vendor_name, **record}
