#!/usr/bin/env python3
"""Inventory the locally running lab workers; optionally run a real RNAfold.

    python scripts/proto_labs.py
    python scripts/proto_labs.py --fold GGGAAACCC
    python scripts/proto_labs.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from rnaharness.proto import labtools  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fold", default="", help="run ViennaRNA RNAfold on this sequence")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inv = labtools.inventory()
    out = {"inventory": inv}
    if args.fold:
        out["fold"] = labtools.vienna_fold(args.fold)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("lab workers (local loopback, no API key): %d/%d ready"
              % (len(inv["ready"]), inv["n_total"]))
        print("  ready:      " + (", ".join(inv["ready"]) or "(none)"))
        print("  degraded:   " + (", ".join(inv["degraded"]) or "(none)"))
        print("  unreachable:" + (", ".join(inv["unreachable"]) or "(none)"))
        print("  key-only:   " + ", ".join(sorted(inv["key_only"])))
        if args.fold:
            f = out["fold"]
            if f["ok"]:
                d = f["data"]
                print("\nRNAfold %s -> %s  MFE %.2f kcal/mol (job %s)"
                      % (d["sequence"], d["structure"], d["mfe"], d["job_id"]))
            else:
                print("\nRNAfold failed: %s" % f["error"]["message"])
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
