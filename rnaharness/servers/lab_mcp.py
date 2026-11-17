"""lab_mcp — MCP server exposing the RomanCohort lab API (PLAN.md §2.5).

Two layers, deliberately:

  generic   lab_run / lab_status / lab_files   reach all 14 hosted tools
  typed     vienna / rhofold / ncbp / gatsite / gemorna / rnadvisor
            only the RNA-relevant subset, so the agent has fewer wrong turns

Long jobs are never blocking: lab_run returns a run_id immediately and the
agent polls lab_status. The API key lives in this process only and is never
a tool parameter.

Run:  python -m rnaharness.servers.lab_mcp
"""
from __future__ import annotations

import json
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import envelope as env
from ..lab_client import LabClient, LabError, validate_params

SOURCE = "romancohort.lab"
MAX_WAIT_S = 60  # lab_status may block this long; anything longer is polling

client = LabClient()
server = FastMCP(
    "lab_mcp",
    log_level="WARNING",
    instructions=(
        "RomanCohort lab tools. lab_run returns a run_id immediately; poll "
        "lab_status; fetch outputs with lab_files. Long tools never block a "
        "single call. Every result is {ok, data, error, provenance, evidence}; "
        "branch on error.code, not on the message text."
    ),
)

_MANIFEST: dict | None = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _manifest() -> dict:
    """Live manifest, cached after the first success.

    Deliberately does not swallow LabError: a missing key or a dead backend
    must surface as `unauthorized` / `backend_unavailable`, not as a
    misleading "unknown tool".
    """
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = client.tools()
    return _MANIFEST


def reset_manifest_cache() -> None:
    """Tests and long-lived servers can force a re-fetch."""
    global _MANIFEST
    _MANIFEST = None


def _manifest_entry(tool_id: str) -> dict:
    for entry in _manifest().get("tools", []):
        if entry.get("id") == tool_id:
            return entry
    return {}


def _lab_error(exc: LabError, started: float) -> dict:
    prov = env.provenance(SOURCE, started_at=started)
    if exc.status == 0:
        return env.err(env.NETWORK_ERROR, str(exc), prov,
                       detail={"payload": exc.payload})
    return env.from_lab_error(exc.status, exc.payload, prov)


# --------------------------------------------------------------------------
# core dispatch (plain functions so tests can call them without MCP)
# --------------------------------------------------------------------------
def do_run(tool_id: str, params: dict, name: str = "") -> dict:
    started = time.monotonic()
    try:
        entry = _manifest_entry(tool_id)
    except LabError as exc:
        return _lab_error(exc, started)
    if not entry:
        known = sorted(t.get("id", "") for t in _manifest().get("tools", []))
        return env.err(env.INVALID_PARAMS, f"unknown tool {tool_id!r}",
                       env.provenance(SOURCE, started_at=started),
                       detail={"known_tools": known})
    if entry.get("status") and entry["status"] != "ready":
        return env.err(env.BACKEND_UNAVAILABLE,
                       f"tool {tool_id} is {entry['status']}, not ready",
                       env.provenance(SOURCE, started_at=started,
                                      version=str(entry.get("version", ""))))
    problems = validate_params(entry, params)
    if problems:
        return env.err(env.INVALID_PARAMS, "; ".join(problems),
                       env.provenance(SOURCE, started_at=started))
    try:
        res = client.submit(tool_id, params, name)
    except LabError as exc:
        return _lab_error(exc, started)
    prov = env.provenance(SOURCE, started_at=started,
                          version=str(entry.get("version", "")))
    return env.ok({"run_id": res["run_id"], "status": res["status"],
                   "attached": res["attached"]}, prov)


def do_status(run_id: str, wait_s: int = 0) -> dict:
    started = time.monotonic()
    if not run_id:
        return env.err(env.INVALID_PARAMS, "run_id is required",
                       env.provenance(SOURCE, started_at=started))
    deadline = time.monotonic() + max(0, min(int(wait_s or 0), MAX_WAIT_S))
    try:
        data = client.run(run_id)
        while data.get("status") in ("queued", "running") \
                and time.monotonic() < deadline:
            time.sleep(2)
            data = client.run(run_id)
    except LabError as exc:
        return _lab_error(exc, started)
    prov = env.provenance(SOURCE, started_at=started)
    payload = {k: data.get(k) for k in
               ("run_id", "tool", "status", "result", "error", "files",
                "created_at", "finished_at") if k in data}
    if data.get("status") in ("error", "stopped"):
        return env.err(env.BACKEND_UNAVAILABLE,
                       f"run {run_id} ended as {data.get('status')}", prov,
                       detail={"run": payload})
    return env.ok(payload, prov)


def do_files(run_id: str, download: bool = False, out_dir: str = "") -> dict:
    started = time.monotonic()
    if not run_id:
        return env.err(env.INVALID_PARAMS, "run_id is required",
                       env.provenance(SOURCE, started_at=started))
    try:
        if download:
            files = client.download(run_id, out_dir or ("runs/" + run_id))
        else:
            manifest = client.files(run_id, hashes=True)
            files = [{"name": f.get("name"), "downloadable": f.get("downloadable"),
                      "sha256": f.get("sha256"), "size": f.get("size"),
                      "reason": f.get("reason")}
                     for f in manifest.get("files", [])]
    except LabError as exc:
        return _lab_error(exc, started)
    prov = env.provenance(SOURCE, started_at=started)
    bad = [f for f in files if f.get("hash_ok") is False]
    if bad:
        return env.err(env.HASH_MISMATCH,
                       f"{len(bad)} file(s) failed sha256 verification", prov,
                       detail={"files": files})
    return env.ok({"files": files}, prov)


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------
def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    """Typed tools take named args; omit defaults so the manifest check sees
    only what the caller actually set (except vienna/gemorna's free-form
    params, which are meaningful even when empty)."""
    return {k: v for k, v in payload.items() if v not in ("", None, [])}


@server.tool(
    description=(
        "Submit any RomanCohort lab tool and get a run_id back immediately. "
        "use_when: you need one of the 14 hosted tools by id. "
        "not_for: waiting for the result — use lab_status. "
        "params must match the live manifest; unknown fields are rejected. "
        "HTTP 409 means an identical job is already running and its run_id is reused."
    ),
)
def lab_run(tool: str, params: dict, name: str = "") -> dict:
    return do_run(tool, params, name)


@server.tool(
    description=(
        "Check a lab run: status (queued/running/done/error/stopped), inline "
        "result when the tool produces one, and file names. "
        "use_when: you have a run_id from lab_run. "
        "not_for: fetching file bytes — use lab_files. wait_s blocks at most 60 s."
    ),
)
def lab_status(run_id: str, wait_s: int = 0) -> dict:
    return do_status(run_id, wait_s)


@server.tool(
    description=(
        "List a finished run's files, optionally downloading with sha256 "
        "verification. use_when: a run is done and outputs are files (PDB, "
        "CSV, plots). not_for: inline results — use lab_status."
    ),
)
def lab_files(run_id: str, download: bool = False, out_dir: str = "") -> dict:
    return do_files(run_id, download, out_dir)


@server.tool(
    description=(
        "ViennaRNA folding and ensemble calculations (fold / plfold / cofold / "
        "duplex / subopt / eval / alifold / distance). use_when: secondary "
        "structure, ensemble, or RNA-RNA hybridization free energy. "
        "not_for: 3D structure (rhofold) or binding sites (gatsite)."
    ),
)
def vienna(tool: str, params: dict | None = None, name: str = "") -> dict:
    return do_run("vienna", {"tool": tool, "params": params or {}}, name)


@server.tool(
    description=(
        "RhoFold+ 3D structure prediction from an RNA sequence (20-1000 nt). "
        "use_when: you need a 3D model. not_for: secondary structure only, or "
        "scoring an existing structure (rnadvisor). Runtime is minutes."
    ),
)
def rhofold(sequence: str, msa: str = "", name: str = "") -> dict:
    return do_run("rhofold", _drop_empty({"sequence": sequence, "msa": msa}), name)


@server.tool(
    description=(
        "NC-Bench / NCfold non-canonical base-pair probabilities over an "
        "existing .idx/.amt annotation. use_when: scoring non-canonical pairs "
        "for a known sample. not_for: raw PDB input — this does not build "
        "annotations. Provide a sample id, or idx plus amt."
    ),
)
def ncbp(id: str = "", idx: str = "", amt: str = "", top_k: int = 50,
         name: str = "") -> dict:
    body = _drop_empty({"id": id, "idx": idx, "amt": amt, "top_k": top_k})
    return do_run("ncbp", body, name)


@server.tool(
    description=(
        "GATRsite binding-site probabilities for an RNA given a PDB. "
        "use_when: probability that residues sit at a binding site. "
        "not_for: experimental binding constants — this is a model score. "
        "threshold is 0.05-0.95."
    ),
)
def gatsite(pdb: str, threshold: float = 0.5, name: str = "") -> dict:
    return do_run("gatsite", {"pdb": pdb, "threshold": threshold}, name)


@server.tool(
    description=(
        "GEMORNA mRNA CDS / 5'UTR / 3'UTR design or UTR scoring. "
        "use_when: designing or scoring coding and UTR sequences. "
        "not_for: anything outside CDS/UTR design."
    ),
)
def gemorna(mode: str, params: dict | None = None, name: str = "") -> dict:
    return do_run("gemorna", {"mode": mode, "params": params or {}}, name)


@server.tool(
    description=(
        "RNAdvisor scoring of a 3D RNA structure, optionally against a native "
        "reference. use_when: comparing a model to a reference. not_for: "
        "predicting a structure — use rhofold. Preserve metric names and "
        "directions; they are scorer-specific."
    ),
)
def rnadvisor(pdb: str, native_pdb: str = "", scores: list[str] | None = None,
              name: str = "") -> dict:
    body = _drop_empty({"pdb": pdb, "native_pdb": native_pdb, "scores": scores})
    return do_run("rnadvisor", body, name)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
