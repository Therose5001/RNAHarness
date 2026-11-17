"""local_mcp — entity layer as MCP tools (PLAN.md §1.2 P1).

Seven tools, all backed by a local SQLite entity store:

  rna_identify     sequence -> URS / xrefs
  rna_fetch        accession -> sequence + annotations
  mirna_resolve    name -> mature id / seed / species / sequence
  gene_resolve     symbol / old name -> HGNC id / Ensembl / Entrez
  id_map           batch id mapping across databases
  taxonomy_resolve name -> taxid + lineage
  rfam_scan        sequence -> Rfam family hits (needs HMMER + Rfam.cm)

Identity only: no literature evidence, no relationship prediction. When a
source was not loaded the tool says `not_loaded` and reports coverage — it
never guesses from a partial index.

Run:  python -m rnaharness.servers.local_mcp
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import envelope as env
from ..entity import (EntityStore, NotLoaded, alias_key, default_db_path,
                      mirna_candidates, normalize_sequence, sequence_hash)
from ..entity.naming import SequenceError, gene_candidates

SOURCE = "entity"
REPO_ROOT = Path(__file__).resolve().parents[2]

server = FastMCP("local_mcp", log_level="WARNING", instructions=(
    "Local entity resolution. Resolve sequences and names BEFORE searching "
    "literature or predicting relationships. Every result is "
    "{ok,data,error,provenance,evidence}; branch on error.code."
))


# --------------------------------------------------------------------------
# store helpers
# --------------------------------------------------------------------------
def _store() -> EntityStore:
    return EntityStore.open()


def _not_loaded(exc: NotLoaded, started: float) -> dict:
    return env.err(env.NOT_LOADED, str(exc),
                   env.provenance(SOURCE, started_at=started),
                   detail=dict(exc.coverage(),
                               hint="python scripts/entity_build.py --help"))


def _prov(store: EntityStore, started: float) -> dict:
    meta = store.meta()
    snaps = sorted({v for k, v in meta.items() if k.startswith("snapshot:")})
    return env.provenance(SOURCE, snapshot=",".join(snaps),
                          started_at=started)


def _coverage(store: EntityStore) -> dict:
    meta = store.meta()
    return {
        "sources": {k[len("source:"):]: v for k, v in meta.items()
                    if k.startswith("source:")},
        "snapshots": {k[len("snapshot:"):]: v for k, v in meta.items()
                      if k.startswith("snapshot:")},
    }


def _entity_payload(store: EntityStore, row: dict, with_sequence: bool = False) -> dict:
    payload = {
        "kind": row["kind"],
        "id": row["canonical_id"],
        "name": row.get("name"),
        "species": row.get("species"),
        "aliases": [a["alias"] for a in store.aliases_for(row["id"])],
        "xrefs": store.xrefs_for(row["id"]),
    }
    if with_sequence and row.get("sequence"):
        payload["sequence"] = row["sequence"]
        payload["length"] = len(row["sequence"])
    return payload


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
@server.tool(description=(
    "Identify an RNA sequence against the local RNAcentral snapshot. "
    "use_when: you have a sequence and need its URS id and cross-references. "
    "not_for: fuzzy homology search (blast_search, P1.5) — this is exact "
    "sequence identity after T->U normalisation. A miss reports coverage; "
    "it is not proof the RNA is unknown."
))
def rna_identify(sequence: str) -> dict:
    started = time.monotonic()
    try:
        seq = normalize_sequence(sequence)
    except SequenceError as exc:
        return env.err(env.INVALID_PARAMS, str(exc),
                       env.provenance(SOURCE, started_at=started))
    try:
        store = _store()
    except NotLoaded as exc:
        return _not_loaded(exc, started)
    try:
        rows = store.by_sequence(sequence_hash(seq))
        prov = _prov(store, started)
        if not rows:
            return env.ok({
                "matches": [],
                "normalized": seq,
                "coverage": _coverage(store),
                "note": "exact sequence match only",
            }, prov)
        return env.ok({
            "matches": [_entity_payload(store, r, with_sequence=True) for r in rows],
            "normalized": seq,
            "coverage": _coverage(store),
        }, prov)
    finally:
        store.close()


@server.tool(description=(
    "Fetch an entity by accession (URS / MIMAT / HGNC: / taxid / any indexed "
    "xref) and return its sequence when available. use_when: you already have "
    "an identifier. not_for: name lookup — use mirna_resolve / gene_resolve."
))
def rna_fetch(accession: str) -> dict:
    started = time.monotonic()
    if not accession.strip():
        return env.err(env.INVALID_PARAMS, "accession is required",
                       env.provenance(SOURCE, started_at=started))
    try:
        store = _store()
    except NotLoaded as exc:
        return _not_loaded(exc, started)
    try:
        rows = store.by_accession(accession.strip())
        prov = _prov(store, started)
        if not rows:
            return env.err(env.UNRESOLVED, f"accession {accession!r} not in the local store",
                           prov, detail={"coverage": _coverage(store)})
        return env.ok({"entities": [_entity_payload(store, r, with_sequence=True)
                                    for r in rows]}, prov)
    finally:
        store.close()


@server.tool(description=(
    "Resolve a miRNA name (hsa-miR-21-5p / miR-21 / MIR21) to its mature "
    "entry: mature id, species, arm, seed and sequence. use_when: any miRNA "
    "name appears in a question. not_for: target prediction."
))
def mirna_resolve(name: str) -> dict:
    started = time.monotonic()
    candidates = mirna_candidates(name)
    if not candidates:
        return env.err(env.INVALID_PARAMS, "name is required",
                       env.provenance(SOURCE, started_at=started))
    try:
        store = _store()
    except NotLoaded as exc:
        return _not_loaded(exc, started)
    try:
        rows = store.by_alias(candidates, kind="mirna")
        prov = _prov(store, started)
        if not rows:
            return env.err(env.UNRESOLVED, f"miRNA {name!r} not found",
                           prov, detail={"candidates": candidates,
                                         "coverage": _coverage(store)})
        results = []
        for row in rows:
            seq = row.get("sequence") or ""
            item = _entity_payload(store, row, with_sequence=True)
            item["seed_7mer"] = seq[1:8] if len(seq) >= 8 else None
            item["seed_6mer"] = seq[1:7] if len(seq) >= 7 else None
            item["arm"] = _arm(row.get("name") or name)
            results.append(item)
        return env.ok({"matches": results, "candidates": candidates}, prov)
    finally:
        store.close()


def _arm(name: str) -> str | None:
    spaced = name.lower()
    if spaced.endswith("-5p") or spaced.endswith("5p"):
        return "5p"
    if spaced.endswith("-3p") or spaced.endswith("3p"):
        return "3p"
    return None


@server.tool(description=(
    "Resolve a gene symbol, alias or previous name to its HGNC record "
    "(HGNC id, canonical symbol, Ensembl / NCBI Gene / UniProt xrefs). "
    "use_when: any gene symbol appears — names are ambiguous without this. "
    "not_for: non-human genes (HGNC covers human only)."
))
def gene_resolve(symbol: str) -> dict:
    started = time.monotonic()
    candidates = gene_candidates(symbol)
    if not candidates:
        return env.err(env.INVALID_PARAMS, "symbol is required",
                       env.provenance(SOURCE, started_at=started))
    try:
        store = _store()
    except NotLoaded as exc:
        return _not_loaded(exc, started)
    try:
        rows = store.by_alias(candidates, kind="gene")
        prov = _prov(store, started)
        if not rows:
            return env.err(env.UNRESOLVED, f"gene {symbol!r} not found in HGNC",
                           prov, detail={"candidates": candidates,
                                         "coverage": _coverage(store)})
        return env.ok({"matches": [_entity_payload(store, r) for r in rows],
                       "candidates": candidates}, prov)
    finally:
        store.close()


@server.tool(description=(
    "Map a batch of identifiers to another database. "
    "use_when: you have URS/MIMAT/HGNC/Ensembl/Entrez ids and need the "
    "equivalent in another namespace. not_for: name resolution."
))
def id_map(ids: list[str], to_db: str, from_db: str = "") -> dict:
    started = time.monotonic()
    clean = [str(i).strip() for i in ids if str(i).strip()]
    if not clean or not to_db.strip():
        return env.err(env.INVALID_PARAMS, "ids and to_db are required",
                       env.provenance(SOURCE, started_at=started))
    try:
        store = _store()
    except NotLoaded as exc:
        return _not_loaded(exc, started)
    try:
        mapped = store.map_ids(clean, to_db.strip(), from_db.strip() or None)
        prov = _prov(store, started)
        resolved = {m["input"] for m in mapped}
        unresolved = [i for i in clean if i not in resolved]
        if not mapped:
            return env.err(env.UNRESOLVED, "no identifiers mapped", prov,
                           detail={"unresolved": unresolved,
                                   "coverage": _coverage(store)})
        return env.ok({"mapping": mapped, "unresolved": unresolved}, prov)
    finally:
        store.close()


@server.tool(description=(
    "Resolve a species name to NCBI taxids with full lineage. "
    "use_when: a question names an organism; never mix species silently. "
    "not_for: strain-level identification."
))
def taxonomy_resolve(name: str) -> dict:
    started = time.monotonic()
    key = alias_key(name)
    if not key:
        return env.err(env.INVALID_PARAMS, "name is required",
                       env.provenance(SOURCE, started_at=started))
    try:
        store = _store()
    except NotLoaded as exc:
        return _not_loaded(exc, started)
    try:
        rows = store.by_alias([key], kind="taxon")
        prov = _prov(store, started)
        if not rows:
            return env.err(env.UNRESOLVED, f"taxon {name!r} not found", prov,
                           detail={"coverage": _coverage(store)})
        results = []
        for row in rows[:10]:
            results.append({
                "taxid": row["canonical_id"],
                "name": row.get("name"),
                "lineage": store.taxonomy_lineage(row["canonical_id"]),
            })
        return env.ok({"matches": results,
                       "ambiguous": len(rows) > 1}, prov)
    finally:
        store.close()


# --------------------------------------------------------------------------
# rfam_scan
# --------------------------------------------------------------------------
def _decode(data: bytes) -> str:
    """wsl.exe mixes UTF-16 diagnostics with UTF-8 tool output."""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-16", errors="replace")


def _rfam_command() -> list[str]:
    """Infernal cmscan by default; RFAM_SCAN_CMD overrides (e.g. a WSL
    wrapper). HMMSCAN_CMD is accepted as a legacy alias."""
    raw = os.environ.get("RFAM_SCAN_CMD") or os.environ.get("HMMSCAN_CMD")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return [os.environ.get("RFAM_SCAN_BIN", "cmscan")]


def _rfam_cm() -> Path:
    return Path(os.environ.get("RFAM_CM") or REPO_ROOT / "data" / "external" / "rfam" / "Rfam.cm")


def parse_tblout(text: str) -> list[dict]:
    """Parse Infernal `cmscan --tblout`; keep the best hit per family.

    Columns (per the tblout header): target name, target accession, query
    name, query accession, mdl, mdl from, mdl to, seq from, seq to, strand,
    trunc, pass, gc, bias, score, E-value, inc, description.
    """
    best: dict[str, dict] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split()
        if len(f) < 17:
            continue
        try:
            score = float(f[14])
            evalue = float(f[15])
            seq_from = int(f[7])
            seq_to = int(f[8])
        except ValueError:
            continue
        family, accession = f[0], f[1]
        description = " ".join(f[17:]) if len(f) > 17 else ""
        hit = {
            "family": family,
            "accession": accession,
            "description": description,
            "evalue": evalue,
            "score": score,
            "strand": f[9],
            "seq_from": min(seq_from, seq_to),
            "seq_to": max(seq_from, seq_to),
            "truncated": f[10] not in ("no", "-"),
        }
        prev = best.get(family)
        if prev is None or evalue < prev["evalue"]:
            best[family] = hit
    return sorted(best.values(), key=lambda h: h["evalue"])


@server.tool(description=(
    "Scan an RNA sequence against the local Rfam covariance models "
    "(Infernal cmscan --rfam). use_when: you need the RNA family/type of a "
    "sequence. not_for: exact sequence lookup (rna_identify) or structure "
    "prediction. Requires RFAM_CM and cmscan; missing dependencies return "
    "dependency_missing, not an empty result."
))
def rfam_scan(sequence: str, evalue: float = 1e-3, use_ga: bool = True,
              max_hits: int = 25) -> dict:
    started = time.monotonic()
    try:
        seq = normalize_sequence(sequence)
    except SequenceError as exc:
        return env.err(env.INVALID_PARAMS, str(exc),
                       env.provenance(SOURCE, started_at=started))
    cm = _rfam_cm()
    if not cm.exists():
        return env.err(env.DEPENDENCY_MISSING, "Rfam.cm not found",
                       env.provenance(SOURCE, started_at=started),
                       detail={"RFAM_CM": str(cm)})
    command = _rfam_command()
    if shutil.which(command[0]) is None and not Path(command[0]).exists():
        return env.err(env.DEPENDENCY_MISSING, f"{command[0]} not on PATH",
                       env.provenance(SOURCE, started_at=started),
                       detail={"RFAM_SCAN_CMD": command})
    with tempfile.TemporaryDirectory(prefix="rna-rfam-") as tmp:
        fa = Path(tmp) / "query.fa"
        tbl = Path(tmp) / "hits.tbl"
        fa.write_text(f">query\n{seq}\n", encoding="ascii")
        argv = command + ["--rfam", "--noali", "--tblout", str(tbl)]
        if use_ga:
            argv.append("--cut_ga")
        argv += [str(cm), str(fa)]
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            return env.err(env.TIMEOUT, "cmscan timed out",
                           env.provenance(SOURCE, started_at=started))
        if not tbl.exists():
            return env.err(env.BACKEND_UNAVAILABLE,
                           "cmscan produced no tblout",
                           env.provenance(SOURCE, started_at=started),
                           detail={"stderr": _decode(proc.stderr)[-1000:]})
        hits = parse_tblout(tbl.read_text(encoding="utf-8", errors="replace"))
    prov = env.provenance(SOURCE, started_at=started,
                          snapshot=os.environ.get("RFAM_SNAPSHOT", ""))
    if not use_ga:
        hits = [h for h in hits if h["evalue"] <= evalue]
    return env.ok({
        "hits": hits[:max_hits],
        "n_hits": len(hits),
        "truncated": len(hits) > max_hits,
        "cutoff": "GA" if use_ga else f"E<={evalue}",
        "rfam_cm": str(cm),
    }, prov)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
