#!/usr/bin/env python3
"""RNAHarness offline prototype: no RNAQwen, no API key, no GPU.

    python scripts/proto_answer.py --mirna miR-21 --gene PTEN --target <50nt window>
    python scripts/proto_answer.py --demo
    python scripts/proto_answer.py --selftest

The retrieval/relation slot that RNAQwen will occupy is deliberately filled by
simple, explainable algorithms: FTS5 literature search + seed-match scoring.
Everything is local and deterministic; no network calls are made.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RNAHARNESS_ENTITY_DB", str(ROOT / "data" / "entities.sqlite"))
os.environ.setdefault("RNAHARNESS_EVIDENCE_DB", str(ROOT / "data" / "evidence.sqlite"))

from rnaharness.proto import answer  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEMO = {
    "mirna": "UAGCUUAUCAGACUGAUGUUGA",  # hsa-miR-21-5p mature sequence
    "gene": "PTEN",
    "target": "A" * 50,
    "species": "hsa",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mirna", default="", help="mature miRNA SEQUENCE (not a name)")
    ap.add_argument("--mirna-name", default="", help="name used for literature retrieval")
    ap.add_argument("--gene", default="")
    ap.add_argument("--target", default="", help="50 nt target window (RNA or DNA)")
    ap.add_argument("--species", default="hsa")
    ap.add_argument("--window", type=int, default=50)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the full envelope")
    ap.add_argument("--fold", action="store_true",
                     help="run a real ViennaRNA RNAfold on --target")
    ap.add_argument("--labs", action="store_true",
                     help="include live inventory of local lab workers")
    args = ap.parse_args(argv)

    if args.selftest:
        from rnaharness.proto import selftest
        report = selftest.run()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    q = dict(DEMO) if args.demo else {}
    for key in ("mirna", "gene", "target", "species"):
        if getattr(args, key):
            q[key] = getattr(args, key)
    if not q.get("mirna") or not q.get("gene"):
        ap.error("--mirna and --gene are required (or use --demo)")
    mirna_name = args.mirna_name or (args.demo and "miR-21") or q["mirna"]

    result = answer.answer_pair(
        mirna=q["mirna"], gene=q["gene"], target=q.get("target", ""),
        species=q.get("species", "hsa"), window=args.window, step=args.step,
        mirna_name=mirna_name, with_fold=args.fold, with_labs=args.labs,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["data"]["answer_text"])
        print()
        print("--- provenance ---")
        print(json.dumps(result["provenance"], ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
