"""Generic adapter for the locally running lab workers.

Contracts are DISCOVERED from each worker's /openapi.json at call time, never
hard-coded: the /api/run or /api/predict request schema (required fields and types) is
resolved from components/schemas, the payload is validated locally, then the
job is submitted and polled. This is the "compute" layer of PLAN.md §2.2.

All traffic stays on 127.0.0.1 via a proxy-free opener. No API key, no egress.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .. import envelope as env
from .labtools import TOOLS, OPENER

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_POLL_S = 0.5


def _get(port: int, path: str, timeout: float = 8.0) -> tuple[int, str]:
    with OPENER.open("http://127.0.0.1:%d%s" % (port, path),
                     timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _post(port: int, path: str, payload: dict,
          timeout: float = 15.0) -> tuple[int, str]:
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _resolve(schema: Any, spec: dict, depth: int = 0) -> Any:
    if not isinstance(schema, dict) or depth > 8:
        return schema
    if "$ref" in schema:
        key = schema["$ref"].split("/")[-1]
        return _resolve(spec.get("components", {}).get("schemas", {}).get(key, {}),
                        spec, depth + 1)
    out = dict(schema)
    if "properties" in schema:
        out["properties"] = {k: _resolve(v, spec, depth + 1)
                             for k, v in schema["properties"].items()}
    if "items" in schema:
        out["items"] = _resolve(schema["items"], spec, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            out[key] = [_resolve(s, spec, depth + 1) for s in schema[key]]
    return out


def contract(name: str) -> dict:
    """Resolve an explicit compute endpoint; never select arbitrary POST routes."""
    started = time.monotonic()
    if name not in TOOLS:
        return env.err(env.NOT_FOUND, "unknown tool %r" % name,
                       env.provenance("lab.adapter", started_at=started))
    port = TOOLS[name][0]
    try:
        status, raw = _get(port, "/openapi.json")
        spec = json.loads(raw)
        paths = spec.get("paths", {})
        run_path = next((p for p in ("/api/run", "/api/predict")
                         if paths.get(p, {}).get("post")), None)
        post = paths.get(run_path, {}).get("post", {})
        if not post:
            return env.err(env.NOT_FOUND, "%s has no supported compute endpoint" % name,
                           env.provenance("lab.adapter", started_at=started,
                                          port=port))
        contents = post.get("requestBody", {}).get("content", {})
        schema = next(iter(contents.values()), {}).get("schema", {})
        resolved = _resolve(schema, spec)
        props = resolved.get("properties", {}) or {}
        required = set(resolved.get("required", []) or [])
        fields = {}
        for key, val in props.items():
            types = []
            for branch in [val] + (val.get("anyOf", []) or []):
                if isinstance(branch, dict) and branch.get("type"):
                    types.append(branch["type"])
            fields[key] = {
                "type": "/".join(dict.fromkeys(types)) or "any",
                "required": key in required,
                "default": val.get("default"),
                "free_form": bool(val.get("additionalProperties")),
            }
        return env.ok({
            "tool": name, "port": port,
            "title": spec.get("info", {}).get("title"),
            "run_path": run_path,
            "request_schema": resolved,
            "fields": fields,
            "required": sorted(required),
            "post_paths": sorted(p for p, v in spec.get("paths", {}).items()
                                 if "post" in v),
            "get_paths": sorted(p for p, v in spec.get("paths", {}).items()
                                if "get" in v),
        }, env.provenance("lab.adapter", started_at=started, port=port,
                          discovery="openapi.json"))
    except Exception as exc:
        return env.err(env.BACKEND_UNAVAILABLE,
                       "%s OpenAPI discovery failed: %s: %s"
                       % (name, type(exc).__name__, exc),
                       env.provenance("lab.adapter", started_at=started,
                                      port=port))


_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "array": list, "object": dict,
}


def validate_payload(name: str, payload: dict) -> dict:
    """Validate against the DISCOVERED schema. Returns {ok, errors, contract}."""
    c = contract(name)
    if not c["ok"]:
        return {"ok": False, "errors": [c["error"]["message"]], "contract": c}
    fields = c["data"]["fields"]
    schema = c["data"]["request_schema"]
    errors = []
    for key in c["data"]["required"]:
        if key not in payload or payload[key] in (None, ""):
            errors.append("missing required field: %s" % key)
    for key, value in payload.items():
        if key not in fields:
            if not schema.get("additionalProperties", True):
                errors.append("unknown field: %s" % key)
            continue
        f = fields[key]
        if f["free_form"]:
            continue
        types = [t for t in f["type"].split("/") if t in _TYPE_MAP]
        if types and not isinstance(value, _TYPE_MAP[types[0]]):
            # bool is an int subclass in Python; reject that silently
            if not (types[0] == "integer" and isinstance(value, bool)):
                errors.append("field %s: expected %s, got %s"
                              % (key, f["type"], type(value).__name__))
    return {"ok": not errors, "errors": errors, "contract": c}


def capabilities(name: str) -> dict:
    """Read-only capability catalogs (/api/tools, /api/modes, ...)."""
    started = time.monotonic()
    if name not in TOOLS:
        return env.err(env.NOT_FOUND, "unknown tool %r" % name,
                       env.provenance("lab.adapter", started_at=started))
    port = TOOLS[name][0]
    paths = ("/api/tools", "/api/modes", "/api/presets", "/api/samples",
             "/api/scores", "/api/cancers", "/api/models", "/api/datasets",
             "/api/schema")
    found = {}
    for path in paths:
        try:
            status, raw = _get(port, path, timeout=4)
            if status == 200:
                found[path] = json.loads(raw)
        except Exception:
            pass
    return env.ok({"tool": name, "port": port, "endpoints": found},
                  env.provenance("lab.adapter", started_at=started, port=port,
                                 discovery="capability-endpoints"))


def run_tool(name: str, payload: dict, timeout_s: float = DEFAULT_TIMEOUT_S,
             poll_s: float = DEFAULT_POLL_S, dry_run: bool = False) -> dict:
    """Submit the discovered compute route and poll a real job result."""
    started = time.monotonic()
    check = validate_payload(name, payload)
    if not check["ok"]:
        return env.err(env.INVALID_PARAMS,
                       "payload rejected by %s's OpenAPI schema: %s"
                       % (name, "; ".join(check["errors"])),
                       env.provenance("lab.adapter", started_at=started),
                       detail={"errors": check["errors"],
                               "required": (check["contract"].get("data") or {}).get("required", [])})
    run_path = check["contract"]["data"]["run_path"]
    if dry_run:
        return env.ok({
            "tool": name, "dry_run": True, "payload": payload,
            "would_post_to": run_path,
            "schema_fields": check["contract"]["data"]["fields"],
        }, env.provenance("lab.adapter", started_at=started,
                          validation="openapi", submitted=False))
    port = TOOLS[name][0]
    try:
        status, raw = _post(port, run_path, payload)
        sub = json.loads(raw)
        jid = sub.get("job_id")
        if not isinstance(jid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", jid):
            return env.err(env.BACKEND_UNAVAILABLE,
                           "no job_id in response: %s" % raw[:300],
                           env.provenance("lab.adapter", started_at=started,
                                          port=port))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            _, result_raw = _get(port, "/api/result/" + jid, timeout=10)
            data = json.loads(result_raw)
            if data.get("status") == "done":
                return env.ok({
                    "tool": name, "job_id": jid, "payload": payload,
                    "result": data.get("result"), "stdout": data.get("stdout"),
                    "elapsed": data.get("elapsed"), "scores": data.get("scores"),
                    "files": data.get("files"),
                    "artifacts": [{"worker": name, "job_id": jid, "name": filename}
                                  for filename in (data.get("files") or [])
                                  if name == "rhofold" and isinstance(filename, str)
                                  and re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.pdb", filename)],
                }, env.provenance("lab.adapter", started_at=started, port=port,
                                  endpoint=run_path,
                                  job_id=jid, worker_version=
                                  check["contract"]["data"]["title"]))
            if data.get("status") in ("error", "failed"):
                return env.err(env.BACKEND_UNAVAILABLE,
                               "%s job %s failed" % (name, jid),
                               env.provenance("lab.adapter", started_at=started,
                                              port=port, job_id=jid),
                               detail=data)
            time.sleep(poll_s)
        return env.err(env.TIMEOUT, "%s job %s did not finish in %ss"
                       % (name, jid, timeout_s),
                       env.provenance("lab.adapter", started_at=started,
                                      port=port, job_id=jid))
    except urllib.error.HTTPError as exc:
        return env.err(env.BACKEND_UNAVAILABLE, "%s HTTP %s" % (name, exc.code),
                       env.provenance("lab.adapter", started_at=started,
                                      port=port),
                       detail=exc.read(400).decode("utf-8", "replace"))
    except Exception as exc:
        return env.err(env.NETWORK_ERROR, "%s: %s" % (type(exc).__name__, exc),
                       env.provenance("lab.adapter", started_at=started,
                                      port=port))
