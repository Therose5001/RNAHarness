#!/usr/bin/env python3
"""Discover lab worker contracts and run jobs through the generic adapter.

    python scripts/proto_adapter.py --list
    python scripts/proto_adapter.py --contract vienna
    python scripts/proto_adapter.py --caps vienna
    python scripts/proto_adapter.py --run vienna --payload '{"tool":"fold","params":{"sequence":"GGGAAACCC"}}'
    python scripts/proto_adapter.py --run vienna --payload '{"tool":"fold"}' --dry-run
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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rnaharness.proto import adapter, labtools  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="inventory + contract status")
    ap.add_argument("--contract", metavar="TOOL")
    ap.add_argument("--caps", metavar="TOOL")
    ap.add_argument("--run", metavar="TOOL")
    ap.add_argument("--payload", default="{}", help="JSON request body")
    ap.add_argument("--dry-run", action="store_true", help="validate only, do not submit")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        inv = labtools.inventory()
        rows = []
        for row in inv["tools"]:
            c = adapter.contract(row["tool"]) if row["reachable"] else None
            rows.append({
                "tool": row["tool"], "health": row["status"],
                "reachable": row["reachable"],
                "openapi": c["ok"] if c else False,
                "required": c["data"]["required"] if (c and c["ok"]) else [],
                "post_paths": c["data"]["post_paths"] if (c and c["ok"]) else [],
            })
        print(json.dumps({"inventory": inv, "contracts": rows},
                         ensure_ascii=False, indent=2))
        return 0
    if args.contract:
        print(json.dumps(adapter.contract(args.contract), ensure_ascii=False, indent=2))
        return 0
    if args.caps:
        print(json.dumps(adapter.capabilities(args.caps), ensure_ascii=False, indent=2))
        return 0
    if args.run:
        try:
            payload = json.loads(args.payload)
        except ValueError as exc:
            print("invalid --payload JSON: %s" % exc, file=sys.stderr)
            return 2
        r = adapter.run_tool(args.run, payload, timeout_s=args.timeout,
                             dry_run=args.dry_run)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
