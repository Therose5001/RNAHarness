#!/usr/bin/env python3
"""Ingest documents and relations into the local evidence index.

Document JSONL, one object per line:

    {"doc_id": "PMC123", "pmid": "99900001", "pmcid": "PMC123", "doi": "...",
     "title": "...", "journal": "...", "year": 2024, "license": "CC-BY",
     "source": "europepmc",
     "passages": [{"passage_id": "PMC123:results:1", "section": "results",
                   "text": "...", "char_start": 0, "char_end": 120}]}

Relation JSONL:

    {"relation_id": "rel1", "mirna_id": "MIMAT0000076", "gene_id": "HGNC:9588",
     "relation_type": "targeting", "method": "CLASH", "doc_id": "PMC123",
     "passage_id": "PMC123:results:1", "confidence": "explicit",
     "extracted_by": "manual", "species": "hsa", "tax_id": "9606",
     "mirna_arm": "5p", "polarity": "positive",
     "experimental_method": "CLASH", "curation_status": "reviewed",
     "extraction_method": "curation"}

Schema v2 adds the species, tax ID, arm, polarity, experimental method,
curation, and extraction fields shown above. Omit unknown metadata instead of
inventing it. Legacy two- and three-column PubMed pair TSVs remain accepted by
``pubmed_ingest.py``. Relations containing only schema-v1 fields remain
accepted by v1 stores. A relation carrying schema-v2 metadata is rejected
before any document or relation write when the target store is v1; read-only
opens never migrate existing stores.

Usage:
    python scripts/evidence_ingest.py --db data/evidence.sqlite \
        --snapshot 2026-11-03 --documents docs.jsonl --relations relations.jsonl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnaharness.evidence import EvidenceStore  # noqa: E402


def _iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{i}: invalid JSON: {exc}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/evidence.sqlite")
    ap.add_argument("--snapshot", default=dt.date.today().isoformat())
    ap.add_argument("--documents")
    ap.add_argument("--relations")
    args = ap.parse_args()
    if not args.documents and not args.relations:
        ap.error("give --documents and/or --relations")

    relation_rows = (
        list(_iter_jsonl(Path(args.relations))) if args.relations else []
    )
    store = EvidenceStore.open(args.db, create=True)
    counts = Counter()
    try:
        store.preflight_relations(relation_rows)
        if args.documents:
            for doc in _iter_jsonl(Path(args.documents)):
                doc.setdefault("snapshot", args.snapshot)
                store.upsert_document(doc)
                counts["documents"] += 1
                counts[f"source:{doc.get('source') or 'unknown'}"] += 1
                for passage in doc.get("passages", []):
                    store.add_passage(
                        passage["passage_id"], doc["doc_id"],
                        passage.get("section", ""), passage["text"],
                        passage.get("char_start"), passage.get("char_end"))
                    counts["passages"] += 1
        for rel in relation_rows:
            store.add_relation(rel)
            counts["relations"] += 1
        for key, value in counts.items():
            if key.startswith("source:"):
                store.set_meta(key, str(value))
        store.set_meta("snapshot:index", args.snapshot)
        store.set_meta("built_at", dt.datetime.now().isoformat(timespec="seconds"))
        store.commit()
    finally:
        info = store.coverage()
        store.close()

    print(json.dumps({"ingested": dict(counts), "store": info},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
