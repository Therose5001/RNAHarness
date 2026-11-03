"""Unified tool return envelope (PLAN.md §2.2).

Every tool returns the same shape so the kernel never has to remember
per-tool field names:

    {"ok": bool, "data": {}, "error": null | {...},
     "provenance": {...}, "evidence": []}

`error.code` is a stable string, not a sentence. The kernel branches on it;
humans read `message`.
"""
from __future__ import annotations

import time
from typing import Any

# Stable error codes. Add here, never invent ad-hoc strings downstream.
BAD_REQUEST = "bad_request"
INVALID_PARAMS = "invalid_params"
UNAUTHORIZED = "unauthorized"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
QUOTA_EXHAUSTED = "quota_exhausted"
BACKEND_UNAVAILABLE = "backend_unavailable"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
HASH_MISMATCH = "hash_mismatch"
UNKNOWN = "unknown"

RETRYABLE = {BACKEND_UNAVAILABLE, TIMEOUT, NETWORK_ERROR}


def provenance(source: str, version: str = "", snapshot: str = "",
               started_at: float | None = None, **extra: Any) -> dict:
    """Build the provenance block. `started_at` is time.monotonic()."""
    latency_ms = 0
    if started_at is not None:
        latency_ms = int((time.monotonic() - started_at) * 1000)
    block = {
        "source": source,
        "version": version,
        "snapshot": snapshot,
        "latency_ms": latency_ms,
    }
    block.update(extra)
    return block


def ok(data: Any, prov: dict, evidence: list | None = None) -> dict:
    return {"ok": True, "data": data, "error": None,
            "provenance": prov, "evidence": evidence or []}


def err(code: str, message: str, prov: dict | None = None,
        detail: Any = None, evidence: list | None = None) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": code in RETRYABLE,
            "detail": detail,
        },
        "provenance": prov or {},
        "evidence": evidence or [],
    }


def from_lab_error(status: int, payload: Any, prov: dict | None = None) -> dict:
    """Map a lab API HTTP status to a structured error (PLAN.md §2.5)."""
    if status in (400, 422):
        code = BAD_REQUEST
    elif status == 401:
        code = UNAUTHORIZED
    elif status == 403:
        code = FORBIDDEN
    elif status == 404:
        code = NOT_FOUND
    elif status == 429:
        code = QUOTA_EXHAUSTED
    elif status >= 500:
        code = BACKEND_UNAVAILABLE
    else:
        code = UNKNOWN
    message = _message_from_payload(payload) or f"lab API returned HTTP {status}"
    return err(code, message, prov, detail={"http_status": status, "payload": payload})


def _message_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(payload, str) and payload:
        return payload[:500]
    return ""
