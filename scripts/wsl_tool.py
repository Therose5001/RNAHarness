#!/usr/bin/env python3
"""Run a Linux tool inside WSL, translating Windows paths.

Used because Infernal/HMMER have no native Windows build here. The entity
tool spawns this wrapper; it stays platform-agnostic.

    RFAM_SCAN_CMD='["C:/RNAHarness/.venv/Scripts/python.exe",
                    "C:/RNAHarness/scripts/wsl_tool.py"]'
    WSL_BIN='cmscan'            # default; set to hmmscan etc. if needed
    RFAM_CM='C:/qwen-api/RNAQwen/data/external/rfam/Rfam.cm'

Every argv item that looks like a Windows path is rewritten to /mnt/<drive>/…
before being passed to `wsl … <WSL_BIN>`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu-24.04")
TOOL = os.environ.get("WSL_BIN", "cmscan")
_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def to_wsl_path(arg: str) -> str:
    m = _DRIVE.match(arg)
    if not m:
        return arg
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{m.group(1).lower()}/{rest}"


def main(argv: list[str]) -> int:
    translated = [to_wsl_path(a) for a in argv]
    cmd = ["wsl", "-d", DISTRO, "--", TOOL, *translated]
    # stdin must not be inherited: under an MCP stdio server it is the
    # JSON-RPC pipe, and wsl.exe will happily block on it.
    # wsl.exe emits UTF-16 for its own diagnostics while the tool emits UTF-8;
    # pass bytes through untouched instead of guessing an encoding here.
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
    if proc.stdout:
        sys.stdout.buffer.write(proc.stdout)
    if proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
