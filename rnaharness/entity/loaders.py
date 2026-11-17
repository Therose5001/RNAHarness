"""Loaders for the official export formats.

Each loader is idempotent (upsert), records the snapshot date, and returns
counts. Nothing here downloads data — see scripts/entity_build.py.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import re
from pathlib import Path

from .naming import normalize_sequence, sequence_hash
from .store import EntityStore

BATCH = 5000


def _today() -> str:
    return _dt.date.today().isoformat()


def _batched(store: EntityStore, i: int) -> None:
    if i % BATCH == 0:
        store.commit()


# --------------------------------------------------------------------------
# RNAcentral
# --------------------------------------------------------------------------
def load_rnacentral_jsonl(store: EntityStore, path: str | Path,
                          snapshot: str | None = None) -> dict:
    """Project format: one {"uid": "URS...", "sequence": "..."} per line."""
    snapshot = snapshot or _today()
    count = skipped = 0
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            uid = str(row.get("uid") or row.get("id") or "").strip()
            sequence = row.get("sequence") or row.get("seq") or ""
            if not uid or not sequence:
                skipped += 1
                continue
            try:
                seq = normalize_sequence(sequence)
            except ValueError:
                skipped += 1
                continue
            eid = store.upsert_entity(
                "rna", uid, sequence=seq, seq_hash=sequence_hash(seq),
                source="rnacentral", snapshot=snapshot,
            )
            store.add_xref(eid, "RNAcentral", uid)
            count += 1
            _batched(store, i)
    store.commit()
    store.set_meta("source:rnacentral", f"{count} sequences")
    store.set_meta("snapshot:rnacentral", snapshot)
    return {"source": "rnacentral", "loaded": count, "skipped": skipped,
            "snapshot": snapshot}


def load_rnacentral_fasta(store: EntityStore, path: str | Path,
                          snapshot: str | None = None) -> dict:
    """RNAcentral FASTA: header starts with a URS id (extra fields ignored)."""
    snapshot = snapshot or _today()
    count = skipped = 0
    uid = ""
    chunks: list[str] = []
    index = 0

    def flush() -> None:
        nonlocal count, skipped
        if not uid:
            return
        try:
            seq = normalize_sequence("".join(chunks))
        except ValueError:
            skipped += 1
            return
        eid = store.upsert_entity(
            "rna", uid, sequence=seq, seq_hash=sequence_hash(seq),
            source="rnacentral", snapshot=snapshot,
        )
        store.add_xref(eid, "RNAcentral", uid)
        count += 1

    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if line.startswith(">"):
                flush()
                header = line[1:].strip().split()[0]
                uid = header if re.match(r"URS\d+", header) else ""
                chunks = []
            else:
                chunks.append(line.strip())
            _batched(store, i)
        flush()
    store.commit()
    store.set_meta("source:rnacentral", f"{count} sequences")
    if snapshot:
        store.set_meta("snapshot:rnacentral", snapshot)
    return {"source": "rnacentral", "loaded": count, "skipped": skipped,
            "snapshot": snapshot}


# --------------------------------------------------------------------------
# miRBase
# --------------------------------------------------------------------------
_MIRBASE_ACC = re.compile(r"\b(MIMAT\d+|MIPF\d+)\b")


def load_mirbase(store: EntityStore, mature_fa: str | Path,
                 aliases_txt: str | Path | None = None,
                 snapshot: str | None = None) -> dict:
    """miRBase mature.fa (+ optional aliases.txt).

    Header shape: `>hsa-miR-21-5p MIMAT0000076 Homo sapiens miR-21-5p`.
    """
    snapshot = snapshot or _today()
    count = skipped = 0
    name = ""
    acc = ""
    chunks: list[str] = []

    def flush() -> None:
        nonlocal count, skipped
        if not name or not acc:
            return
        try:
            seq = normalize_sequence("".join(chunks))
        except ValueError:
            skipped += 1
            return
        species = name.split("-", 1)[0] if "-" in name else ""
        eid = store.upsert_entity(
            "mirna", acc, name=name, species=species,
            sequence=seq, seq_hash=sequence_hash(seq),
            source="mirbase", snapshot=snapshot,
        )
        store.add_alias(eid, name, "mirbase")
        store.add_xref(eid, "miRBase", acc)
        count += 1

    with open(mature_fa, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if line.startswith(">"):
                flush()
                header = line[1:].strip()
                parts = header.split()
                name = parts[0]
                m = _MIRBASE_ACC.search(header)
                acc = m.group(1) if m else ""
                chunks = []
            else:
                chunks.append(line.strip())
            _batched(store, i)
        flush()

    aliased = 0
    if aliases_txt:
        by_acc = {row["canonical_id"]: row["id"] for row in store._rows(
            "SELECT id, canonical_id FROM entities WHERE source = 'mirbase'")}
        with open(aliases_txt, encoding="utf-8") as fh:
            for line in fh:
                acc, _, alias = line.rstrip("\n").partition("\t")
                eid = by_acc.get(acc.strip())
                if eid and alias.strip():
                    store.add_alias(eid, alias.strip(), "mirbase-aliases")
                    aliased += 1
    store.commit()
    store.set_meta("source:mirbase", f"{count} mature miRNAs")
    store.set_meta("snapshot:mirbase", snapshot)
    return {"source": "mirbase", "loaded": count, "aliases": aliased,
            "skipped": skipped, "snapshot": snapshot}


# --------------------------------------------------------------------------
# HGNC
# --------------------------------------------------------------------------
def _split_multi(value: str) -> list[str]:
    return [v.strip().strip('"') for v in (value or "").split("|") if v.strip()]


def load_hgnc(store: EntityStore, path: str | Path,
              snapshot: str | None = None) -> dict:
    """HGNC complete set TSV (header-driven; tolerates column reordering)."""
    snapshot = snapshot or _today()
    count = skipped = 0
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for i, row in enumerate(reader, 1):
            hgnc_id = (row.get("hgnc_id") or "").strip()
            symbol = (row.get("symbol") or "").strip()
            if not hgnc_id or not symbol:
                skipped += 1
                continue
            eid = store.upsert_entity(
                "gene", hgnc_id, name=symbol, species="hsa",
                source="hgnc", snapshot=snapshot,
            )
            store.add_alias(eid, symbol, "hgnc-symbol")
            for alias in _split_multi(row.get("alias_symbol", "")):
                store.add_alias(eid, alias, "hgnc-alias")
            for prev in _split_multi(row.get("prev_symbol", "")):
                store.add_alias(eid, prev, "hgnc-prev")
            for ens in _split_multi(row.get("ensembl_gene_id", "")):
                store.add_xref(eid, "Ensembl", ens)
            for entrez in _split_multi(row.get("entrez_id", "")):
                store.add_xref(eid, "NCBI Gene", entrez)
            for uniprot in _split_multi(row.get("uniprot_ids", "")):
                store.add_xref(eid, "UniProt", uniprot)
            count += 1
            _batched(store, i)
    store.commit()
    store.set_meta("source:hgnc", f"{count} genes")
    store.set_meta("snapshot:hgnc", snapshot)
    return {"source": "hgnc", "loaded": count, "skipped": skipped,
            "snapshot": snapshot}


# --------------------------------------------------------------------------
# NCBI Taxonomy
# --------------------------------------------------------------------------
def _dmp_fields(line: str) -> list[str]:
    return [part.strip() for part in line.split("|")]


def load_taxonomy(store: EntityStore, names_dmp: str | Path,
                  nodes_dmp: str | Path | None = None,
                  snapshot: str | None = None) -> dict:
    """names.dmp + optional nodes.dmp. Scientific names become the entity name;
    every other name class becomes an alias."""
    snapshot = snapshot or _today()
    count = skipped = 0
    with open(names_dmp, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            parts = _dmp_fields(line)
            if len(parts) < 4:
                skipped += 1
                continue
            taxid, name, _unique, name_class = parts[0], parts[1], parts[2], parts[3]
            if not taxid or not name:
                skipped += 1
                continue
            eid = store.upsert_entity("taxon", taxid, source="ncbi-taxonomy",
                                      snapshot=snapshot)
            if name_class == "scientific name":
                store.upsert_entity("taxon", taxid, name=name,
                                    source="ncbi-taxonomy", snapshot=snapshot)
            store.add_alias(eid, name, f"ncbi-{name_class.replace(' ', '-')}")
            count += 1
            _batched(store, i)

    parents = 0
    if nodes_dmp:
        with open(nodes_dmp, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                parts = _dmp_fields(line)
                if len(parts) < 2:
                    continue
                store.upsert_entity("taxon", parts[0], parent=parts[1],
                                    source="ncbi-taxonomy", snapshot=snapshot)
                parents += 1
                _batched(store, i)
    store.commit()
    store.set_meta("source:taxonomy", f"{count} names, {parents} nodes")
    store.set_meta("snapshot:taxonomy", snapshot)
    return {"source": "ncbi-taxonomy", "loaded": count, "parents": parents,
            "skipped": skipped, "snapshot": snapshot}
