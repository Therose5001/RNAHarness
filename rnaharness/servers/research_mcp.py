"""Scoped stdio MCP entry point for the shared RNAHarness research skill."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from .. import envelope as env
from .. import tool_imports
from ..evidence.store import EvidenceStore, NotLoaded
from ..proto import adapter
from . import local_mcp

server = FastMCP("rnaharness-research", log_level="WARNING")
_SOURCE = "research_mcp"
_HAIRPINS = {
    "WT": "GGGCGGCAACUAAGCCGCCC",
    "L": "GGAUAGCAACUAAGCCGCCC",
    "R": "GGGCGGCAACUAAGCUAUCC",
    "LR": "GGAUAGCAACUAAGCUAUCC",
}


def _fold_local(variant: str, temperature_c: int) -> dict:
    started = time.monotonic()
    try:
        import RNA
    except ImportError:
        return env.err(env.DEPENDENCY_MISSING, "Install the RNAHarness research extra.",
                       env.provenance("viennarna.local", started_at=started))
    sequence = _HAIRPINS[variant]
    settings = RNA.md()
    settings.temperature = temperature_c
    settings.dangles = 2
    fold = RNA.fold_compound(sequence, settings)
    structure, mfe = fold.mfe()
    fold.exp_params_rescale(mfe)
    _, ensemble = fold.pf()
    job_id = uuid4().hex
    reply = env.ok({"variant": variant, "temperature_c": temperature_c, "dangles": 2,
                    "job_id": job_id, "structure": structure, "mfe": mfe,
                    "ensemble_energy": ensemble, "unit": "kcal/mol"},
                   env.provenance("viennarna.local", version=RNA.__version__,
                                  started_at=started, job_id=job_id))
    directory = Path(os.environ.get("RNAHARNESS_RESEARCH_RUNS", "runs/research"))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / (job_id + ".json")).open("x", encoding="utf-8") as receipt:
            json.dump({"schema_version": 1, "input_sequence": sequence, **reply}, receipt)
    except OSError:
        return env.err(env.BACKEND_UNAVAILABLE, "Could not save the reference fold receipt.",
                       reply["provenance"])
    return reply


@server.tool()
def normalize_prefix(prefix: str) -> dict:
    """Normalize a biomedical database prefix using the approved Bioregistry snapshot."""
    return tool_imports.call_import_tool(tool_imports.TOOL, {"prefix": prefix})


def _human_entity(result: dict) -> dict:
    if not result["ok"]:
        error = result["error"]
        if error["code"] == env.NOT_LOADED:
            return env.err(env.NOT_LOADED, "The human entity snapshot is unavailable.",
                           result["provenance"])
        return result
    keys = ("id", "name", "kind", "species", "arm", "length", "xrefs")
    matches = [{key: row[key] for key in keys if key in row}
               for row in result["data"]["matches"] if row.get("species") == "hsa"]
    if not matches:
        return env.err(env.UNRESOLVED, "The snapshot has no matching human entity.",
                       result["provenance"])
    return env.ok({"matches": matches}, result["provenance"])


@server.tool()
def resolve_human_gene(symbol: str) -> dict:
    """Resolve one human gene symbol or alias to HGNC and available cross-references."""
    symbol = symbol.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", symbol):
        return env.err(env.INVALID_PARAMS, "Supply one human gene symbol or alias.",
                       env.provenance(_SOURCE))
    return _human_entity(local_mcp.gene_resolve(symbol))


@server.tool()
def resolve_human_mirna(name: str) -> dict:
    """Resolve a mature human miRNA name or MIMAT accession to identity and arm metadata."""
    name = name.strip()
    if not re.fullmatch(r"(?:hsa-[A-Za-z0-9.-]{1,64}|MIMAT[0-9]{7})", name, re.I):
        return env.err(env.INVALID_PARAMS, "Supply a human hsa- name or a MIMAT accession.",
                       env.provenance(_SOURCE))
    return _human_entity(local_mcp.mirna_resolve(name))


@server.tool()
def indexed_citation(pmid: str) -> dict:
    """Look up bibliographic metadata and corpus coverage for a PMID in the local index."""
    if not re.fullmatch(r"[1-9][0-9]{0,8}", pmid):
        return env.err(env.INVALID_PARAMS, "Supply a numeric PMID.", env.provenance(_SOURCE))
    start = time.monotonic()
    try:
        store = EvidenceStore.open()
    except NotLoaded:
        return env.err(env.NOT_LOADED, "The citation index is unavailable.",
                       env.provenance(_SOURCE, started_at=start))
    try:
        record = store.get_document(pmid)
        coverage = store.coverage()
    finally:
        store.close()
    prov = env.provenance("evidence", snapshot=",".join(coverage["snapshots"]), started_at=start)
    if record is None or str(record.get("pmid")) != pmid:
        return env.err(env.UNRESOLVED, "This PMID is absent from the local citation index.",
                       prov, detail={"coverage": coverage})
    keys = ("doc_id", "pmid", "pmcid", "title", "year", "doi", "source", "snapshot")
    return env.ok({"citation": {key: record[key] for key in keys if key in record},
                   "coverage": coverage}, prov)


@server.tool()
def fold_reference_hairpin(variant: Literal["WT", "L", "R", "LR"],
                           temperature_c: Literal[37, 55] = 37) -> dict:
    """Run ViennaRNA on one fixed synthetic teaching hairpin at 37 or 55 Celsius."""
    if variant not in _HAIRPINS or temperature_c not in (37, 55):
        return env.err(env.INVALID_PARAMS, "Select a reference variant and temperature.",
                       env.provenance(_SOURCE))
    if os.environ.get("RNAHARNESS_REFERENCE_FOLD_BACKEND") == "local":
        return _fold_local(variant, temperature_c)
    result = adapter.run_tool("vienna", {"tool": "fold", "params": {
        "sequence": _HAIRPINS[variant], "temperature": temperature_c, "dangles": 2,
    }})
    if not result["ok"]:
        error = result["error"]
        return env.err(error["code"], "The ViennaRNA worker did not complete the reference fold.",
                       result["provenance"])
    job = result["data"]
    fold = job.get("result")
    if not isinstance(fold, dict) or not isinstance(fold.get("structure"), str) or fold.get("mfe") is None:
        return env.err(env.BACKEND_UNAVAILABLE, "The worker returned an incomplete fold.",
                       result["provenance"])
    return env.ok({"variant": variant, "temperature_c": temperature_c, "dangles": 2,
                   "job_id": job["job_id"], "structure": fold["structure"], "mfe": fold["mfe"],
                   "ensemble_energy": fold.get("ensemble_energy"), "unit": "kcal/mol"},
                  result["provenance"])


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
