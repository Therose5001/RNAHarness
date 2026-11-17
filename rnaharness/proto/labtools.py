"""Read-only access to the locally running lab workers (no API key).

These are the SAME services behind https://www.romancohort.cloud/api/v1, but
on the dev box they also expose plain loopback HTTP endpoints. Port mapping is
taken from the active nginx config (/etc/nginx/snippets/hosted-tools.conf and
conf.d/romancohort.conf); health endpoints need no authentication.

Important: urllib picks up the machine-wide proxy on Windows. All calls here go
through a proxy-free opener so 127.0.0.1 stays local (verified 2026-11-14).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .. import envelope as env

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TIMEOUT = 4

# tool id -> (local port, health path). LNPDB has no dedicated worker: it is
# query-only through the server-side /api/v1 facade (port 8030 on the VPS),
# which DOES require ROMANCOHORT_API_KEY. Do not pretend it is local.
TOOLS = {
    "rhofold":   (8011, "/health"),  # server 8013 tunnels to dev box 8011
    "physicell": (8014, "/health"),
    "vienna":    (8015, "/health"),
    "gemorna":   (8016, "/health"),
    "rnadvisor": (8017, "/health"),
    "rdock":     (8019, "/health"),
    "haddock3":  (8020, "/health"),
    "gatsite":   (8021, "/health"),
    "signald":   (8022, "/health"),
    "bioease":   (8023, "/health"),
    "ncbp":      (8024, "/health"),
    "esm2":      (8025, "/health"),
    "vaxseer":   (8026, "/health"),
}
KEY_ONLY = {
    "lnpdb": "query-only via /api/v1 on the VPS (needs ROMANCOHORT_API_KEY)",
    "vienna_v1": "job submission via /api/v1 (needs ROMANCOHORT_API_KEY)",
}


def _get(port: int, path: str, timeout: float = TIMEOUT):
    url = "http://127.0.0.1:%d%s" % (port, path)
    with OPENER.open(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def probe_one(name: str) -> dict:
    port, path = TOOLS[name]
    started = time.monotonic()
    try:
        status, body = _get(port, path)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"raw": body[:300]}
        return {
            "tool": name, "port": port, "http_status": status,
            "reachable": True,
            "ok": bool(payload.get("ok", True)),
            "status": payload.get("status"),
            "engine": payload.get("engine") or payload.get("version") or payload.get("rscript"),
            "ms": int((time.monotonic() - started) * 1000),
            "detail": payload,
        }
    except urllib.error.HTTPError as exc:
        return {"tool": name, "port": port, "http_status": exc.code,
                "reachable": True, "ok": False, "status": "http_error",
                "engine": None, "ms": int((time.monotonic() - started) * 1000),
                "detail": exc.read(200).decode("utf-8", "replace")}
    except Exception as exc:
        return {"tool": name, "port": port, "http_status": None,
                "reachable": False, "ok": False, "status": "unreachable",
                "engine": None, "ms": int((time.monotonic() - started) * 1000),
                "detail": type(exc).__name__ + ": " + str(exc)[:160]}


def inventory() -> dict:
    """Live readiness of every local lab worker. Read-only, no key, no writes."""
    rows = [probe_one(n) for n in TOOLS]
    ready = [r["tool"] for r in rows if r["reachable"] and r["ok"] is True
             and r["status"] == "ready"]
    degraded = [r["tool"] for r in rows if r["reachable"] and r["status"] != "ready"]
    down = [r["tool"] for r in rows if not r["reachable"]]
    return {
        "source": "local-loopback-health",
        "no_api_key": True,
        "tools": rows,
        "n_total": len(rows),
        "ready": ready,
        "degraded": degraded,
        "unreachable": down,
        "key_only": KEY_ONLY,
        "note": ("Health probes only. Job submission through /api/v1 needs "
                 "ROMANCOHORT_API_KEY and is not attempted here."),
    }


def vienna_fold(sequence: str, timeout_s: float = 30.0,
                poll_s: float = 0.4) -> dict:
    """Real ViennaRNA RNAfold via the local worker. No key, no network egress.

    POST /api/run -> 202 {job_id}; poll GET /api/result/{job_id} until done.
    Returns the unified envelope. A failure is a structured error, never a
    fabricated structure.
    """
    started = time.monotonic()
    seq = sequence.strip().upper().replace("T", "U")
    if not seq or len(seq) > 2000 or set(seq) - set("ACGUN"):
        return env.err(env.INVALID_PARAMS, "sequence must be 1-2000 nt of ACGUN",
                       env.provenance("vienna.local", started_at=started))
    body = json.dumps({"tool": "fold", "params": {"sequence": seq}}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8015/api/run", data=body,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=10) as r:
            sub = json.loads(r.read().decode("utf-8", "replace"))
        jid = sub.get("job_id")
        if not jid:
            raise ValueError("no job_id in submit response: %r" % sub)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status, raw = _get(8015, "/api/result/" + jid, timeout=10)
            data = json.loads(raw)
            if data.get("status") == "done":
                result = data.get("result") or {}
                return env.ok({
                    "job_id": jid,
                    "tool": "vienna.fold",
                    "sequence": seq,
                    "structure": result.get("structure"),
                    "mfe": result.get("mfe"),
                    "ensemble_energy": result.get("ensemble_energy"),
                    "bpp_pairs": (result.get("bpp") or {}).get("pairs"),
                    "elapsed": data.get("elapsed"),
                    "stdout": data.get("stdout"),
                }, env.provenance("vienna.local", version="2.7.2",
                                  started_at=started, endpoint="8015/api/run",
                                  job_id=jid))
            if data.get("status") in ("error", "failed"):
                return env.err(env.BACKEND_UNAVAILABLE,
                               "ViennaRNA job %s failed" % jid,
                               env.provenance("vienna.local", started_at=started),
                               detail=data)
            time.sleep(poll_s)
        return env.err(env.TIMEOUT, "ViennaRNA job %s did not finish in %ss"
                       % (jid, timeout_s),
                       env.provenance("vienna.local", started_at=started),
                       detail={"job_id": jid})
    except urllib.error.HTTPError as exc:
        return env.err(env.BACKEND_UNAVAILABLE, "ViennaRNA HTTP %s" % exc.code,
                       env.provenance("vienna.local", started_at=started),
                       detail=exc.read(300).decode("utf-8", "replace"))
    except Exception as exc:
        return env.err(env.NETWORK_ERROR, "%s: %s" % (type(exc).__name__, exc),
                       env.provenance("vienna.local", started_at=started))
