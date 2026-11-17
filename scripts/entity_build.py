#!/usr/bin/env python3
"""Build the local entity store from official export files.

Examples
--------
    python scripts/entity_build.py --db data/entities.sqlite \
        --rnacentral-jsonl /path/to/rnacentral_2m.jsonl \
        --mirbase-mature /path/to/mature.fa \
        --mirbase-aliases /path/to/aliases.txt \
        --hgnc /path/to/hgnc_complete_set.txt \
        --taxonomy-dir /path/to/taxdump

Only files you pass are loaded; missing sources stay absent, and tools report
`not_loaded` for them instead of guessing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnaharness.entity import EntityStore  # noqa: E402
from rnaharness.entity.loaders import (  # noqa: E402
    load_hgnc,
    load_mirbase,
    load_rnacentral_fasta,
    load_rnacentral_jsonl,
    load_taxonomy,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/entities.sqlite")
    ap.add_argument("--snapshot", default="", help="override snapshot date (YYYY-MM-DD)")
    ap.add_argument("--rnacentral-jsonl")
    ap.add_argument("--rnacentral-fasta")
    ap.add_argument("--mirbase-mature")
    ap.add_argument("--mirbase-aliases")
    ap.add_argument("--hgnc", help="HGNC complete set TSV")
    ap.add_argument("--taxonomy-dir", help="directory containing names.dmp and nodes.dmp")
    args = ap.parse_args()

    snapshot = args.snapshot or None
    if not any([args.rnacentral_jsonl, args.rnacentral_fasta,
                args.mirbase_mature, args.hgnc, args.taxonomy_dir]):
        ap.error("give at least one source file")

    store = EntityStore.open(args.db, create=True)
    results = []
    try:
        if args.rnacentral_jsonl:
            results.append(load_rnacentral_jsonl(store, args.rnacentral_jsonl, snapshot))
        if args.rnacentral_fasta:
            results.append(load_rnacentral_fasta(store, args.rnacentral_fasta, snapshot))
        if args.mirbase_mature:
            results.append(load_mirbase(store, args.mirbase_mature,
                                        args.mirbase_aliases, snapshot))
        if args.hgnc:
            results.append(load_hgnc(store, args.hgnc, snapshot))
        if args.taxonomy_dir:
            tax = Path(args.taxonomy_dir)
            results.append(load_taxonomy(store, tax / "names.dmp",
                                         tax / "nodes.dmp", snapshot))
        store.set_meta("built_at", __import__("datetime").datetime.now().isoformat(timespec="seconds"))
        store.commit()
    finally:
        info = store.info()
        store.close()

    print(json.dumps({"loads": results, "store": info}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
