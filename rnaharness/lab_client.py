"""stdlib HTTP client for the RomanCohort lab API v1.

This is the logic extracted from ``scripts/lab.py`` (which stays the CLI and
keeps its behaviour) so that MCP servers and tests can share one code path.

Deliberately no third-party dependencies: the CLI promises stdlib-only, and
the MCP server should not make the key handling any harder to audit.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE = os.environ.get("ROMANCOHORT_API_BASE",
                              "https://www.romancohort.cloud/api/v1")
KEY_ENV = "ROMANCOHORT_API_KEY"

# Run states that mean "still going".
ACTIVE_STATES = ("queued", "running", "waiting_external")


class LabError(Exception):
    """HTTP-level failure from the lab API. Carries the parsed payload."""

    def __init__(self, status: int, payload, message: str = ""):
        self.status = status
        self.payload = payload
        super().__init__(message or f"lab API HTTP {status}: {payload}")


class LabClient:
    """Stateless-ish client. Key is read at call time, never stored on disk."""

    def __init__(self, base: str = "", key: str = "", timeout: int = 120):
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self._key = key or ""
        self.timeout = timeout

    # -- auth -----------------------------------------------------------
    def key(self) -> str:
        key = self._key or os.environ.get(KEY_ENV, "")
        if not key:
            raise LabError(401, {"error": f"{KEY_ENV} is not set"},
                           f"{KEY_ENV} is not set")
        return key

    # -- transport ------------------------------------------------------
    def _open(self, method: str, path: str, body=None, timeout=None):
        url = self.base + path
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.key())
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if method == "POST":
            req.add_header("Idempotency-Key", str(uuid.uuid4()))
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw[:1000]}
            raise LabError(exc.code, payload) from None
        except urllib.error.URLError as exc:
            raise LabError(0, {"error": str(exc.reason)},
                           f"network error: {exc.reason}") from None

    def json_call(self, method: str, path: str, body=None, timeout=None) -> dict:
        resp = self._open(method, path, body, timeout)
        try:
            raw = resp.read().decode("utf-8", "replace")
        finally:
            resp.close()
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

    def raw_call(self, path: str, timeout=None):
        """Returns an open response object; caller must close it."""
        return self._open("GET", path, None, timeout)

    # -- endpoints ------------------------------------------------------
    def tools(self) -> dict:
        return self.json_call("GET", "/tools")

    def tool_schema(self, tool_id: str) -> dict:
        """One tool's manifest entry, or {} if absent."""
        for entry in self.tools().get("tools", []):
            if entry.get("id") == tool_id:
                return entry
        return {}

    def health(self) -> dict:
        return self.json_call("GET", "/health")

    def usage(self) -> dict:
        return self.json_call("GET", "/usage")

    def submit(self, tool: str, params: dict, name: str = "") -> dict:
        """POST /runs. Returns {'run_id', 'status', 'attached': bool}.

        A 409 with a run_id means the same key already has an active job for
        this (tool, params); we attach instead of submitting twice.
        """
        body = {"tool": tool, "params": params}
        if name:
            body["name"] = name
        try:
            data = self.json_call("POST", "/runs", body, timeout=90)
        except LabError as exc:
            if exc.status == 409 and isinstance(exc.payload, dict) \
                    and exc.payload.get("run_id"):
                return {"run_id": exc.payload["run_id"],
                        "status": exc.payload.get("status", "running"),
                        "attached": True}
            raise
        run_id = data.get("run_id")
        if not run_id:
            raise LabError(502, data, "submit returned no run_id")
        return {"run_id": run_id, "status": data.get("status", "running"),
                "attached": False}

    def run(self, run_id: str) -> dict:
        return self.json_call("GET", "/runs/" + urllib.parse.quote(run_id))

    def cancel(self, run_id: str) -> dict:
        return self.json_call("POST", "/runs/" + urllib.parse.quote(run_id) + "/cancel")

    def files(self, run_id: str, hashes: bool = True) -> dict:
        return self.json_call(
            "GET", "/runs/%s/files?hash=%d" % (urllib.parse.quote(run_id),
                                               1 if hashes else 0))

    def samples(self, tool: str) -> dict:
        return self.json_call("GET", "/tools/%s/samples" % urllib.parse.quote(tool))

    def lnpdb_search(self, query: dict) -> dict:
        query = {k: v for k, v in query.items() if v not in ("", None)}
        return self.json_call("GET", "/lnpdb/search?" + urllib.parse.urlencode(query))

    # -- file download --------------------------------------------------
    def download(self, run_id: str, out_dir: str | Path,
                 hashes: bool = True) -> list[dict]:
        """Download a run's files into out_dir, verifying sha256.

        Returns one record per file: {name, path, sha256, hash_ok}.
        Non-downloadable entries and HTTP failures are reported in the
        record rather than raised, so one bad file does not lose the rest.
        """
        manifest = self.files(run_id, hashes=hashes)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        results = []
        for item in manifest.get("files", []):
            name = item.get("name", "")
            if not item.get("downloadable"):
                results.append({"name": name, "path": None, "sha256": None,
                                "hash_ok": None,
                                "skipped": item.get("reason", "not downloadable")})
                continue
            rel = item.get("url", "")
            if rel.startswith("/api/v1"):
                rel = rel[len("/api/v1"):]
            dest = out / name.split("/")[-1]
            try:
                resp = self.raw_call(rel, timeout=300)
            except LabError as exc:
                results.append({"name": name, "path": None, "sha256": None,
                                "hash_ok": None,
                                "error": f"HTTP {exc.status}"})
                continue
            digest = hashlib.sha256()
            try:
                with open(dest, "wb") as fh:
                    for chunk in iter(lambda: resp.read(65536), b""):
                        fh.write(chunk)
                        digest.update(chunk)
            finally:
                resp.close()
            got = digest.hexdigest()
            expected = item.get("sha256")
            results.append({"name": name, "path": str(dest), "sha256": got,
                            "hash_ok": (not expected) or got == expected})
        return results


def validate_params(schema_entry: dict, params: dict) -> list[str]:
    """Check params against a manifest entry's input schema.

    Returns a list of human-readable problems; empty means OK. Intentionally
    shallow: types, required keys, enums, and numeric bounds only. The backend
    remains the authority; this just stops the agent inventing fields.
    """
    problems: list[str] = []
    if not isinstance(params, dict):
        return ["params must be a JSON object"]
    schema = schema_entry.get("input_schema") or schema_entry.get("params_schema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for key in required:
        if key not in params:
            problems.append(f"missing required field: {key}")
    if not props:
        return problems  # free-form tool; nothing to check
    for key, value in params.items():
        if key not in props:
            problems.append(f"unknown field: {key} (allowed: {', '.join(sorted(props))})")
            continue
        spec = props[key] or {}
        allowed = spec.get("enum")
        if allowed and value not in allowed:
            problems.append(f"{key}={value!r} not in {allowed}")
        t = spec.get("type")
        if t == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
            problems.append(f"{key} must be an integer")
        elif t == "number" and not isinstance(value, (int, float)):
            problems.append(f"{key} must be a number")
        elif t == "string" and not isinstance(value, str):
            problems.append(f"{key} must be a string")
        elif t == "boolean" and not isinstance(value, bool):
            problems.append(f"{key} must be a boolean")
        elif t == "array" and not isinstance(value, list):
            problems.append(f"{key} must be an array")
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if lo is not None and value < lo:
                problems.append(f"{key}={value} below minimum {lo}")
            if hi is not None and value > hi:
                problems.append(f"{key}={value} above maximum {hi}")
    return problems
