#!/usr/bin/env python3
"""Connect to lab_mcp over stdio and exercise the MCP protocol.

    python scripts/mcp_smoke.py            # list tools only
    python scripts/mcp_smoke.py --run      # also call lab_run (needs the API key)

This is a protocol-level smoke test: it goes through JSON-RPC, not by
importing the server module.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true",
                    help="also call lab_run(vienna, fold) — needs ROMANCOHORT_API_KEY")
    args = ap.parse_args()

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "rnaharness.servers.lab_mcp"],
        cwd=str(ROOT),
        env={"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo")
            print("server:", info.name, getattr(info, "version", ""))
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("tools:", ", ".join(names))
            if args.run:
                result = await session.call_tool(
                    "lab_run", {"tool": "vienna", "params": {"tool": "fold", "params": {"sequence": "ACGUACGU"}}})
                for block in result.content:
                    text = getattr(block, "text", None)
                    if text:
                        print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
                if getattr(result, "is_error", getattr(result, "isError", False)):
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
