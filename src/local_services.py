"""Start the mock vendor-risk API in-process for eval runners and tests.

The starter eval runner assumed `run_local.py` was already running in another
terminal; without it every vendor lookup failed and correct outage handling
looked like a test failure. Runners call `ensure_vendor_api()` so they are
self-contained. The API is served over real HTTP so timeouts, 404s and 503s
behave exactly as they do in the app.
"""
from __future__ import annotations

import os
import socket
import threading
import time

import requests
import uvicorn

from src.config import get_vendor_risk_base_url

_server: uvicorn.Server | None = None
_lock = threading.Lock()


def _healthy(base_url: str) -> bool:
    try:
        return requests.get(f"{base_url}/health", timeout=0.5).ok
    except requests.RequestException:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_vendor_api_in_background(timeout_seconds: float = 30.0) -> str:
    """Start the mock API on a free port in a daemon thread and return its base URL."""
    global _server
    with _lock:
        if _server is not None and _server.started:
            return f"http://127.0.0.1:{_server.config.port}"
        port = _free_port()
        config = uvicorn.Config("mock_api.app:app", host="127.0.0.1", port=port, log_level="warning")
        _server = uvicorn.Server(config)
        threading.Thread(target=_server.run, name="mock-vendor-api", daemon=True).start()
        deadline = time.monotonic() + timeout_seconds
        while not _server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Mock vendor-risk API did not start in time")
            time.sleep(0.05)
        return f"http://127.0.0.1:{port}"


def stop_vendor_api() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.should_exit = True
            _server = None


def ensure_vendor_api() -> str:
    """Use the configured API if it is up; otherwise start one and point the client at it."""
    configured = get_vendor_risk_base_url()
    if _healthy(configured):
        return configured
    url = start_vendor_api_in_background()
    os.environ["VENDOR_RISK_BASE_URL"] = url
    return url
