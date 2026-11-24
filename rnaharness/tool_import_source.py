"""Fetch pinned, data-only sources for the prefix normalization adapter."""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

REPO = "biopragmatics/bioregistry"
GITHUB = f"https://github.com/{REPO}"
API = f"https://api.github.com/repos/{REPO}"
PAPERS = {
    "https://doi.org/10.1038/s41597-022-01807-3",
    "https://www.nature.com/articles/s41597-022-01807-3",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC9675740",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC9675740/",
}
COMMIT = re.compile(r"[a-f0-9]{40}")
PREFIX = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
MAX_SOURCE_BYTES = 16 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SourceUnavailable(ValueError):
    pass


def source_ref(url: str) -> str:
    clean = url.strip().rstrip("/")
    if clean in PAPERS or clean == GITHUB:
        return "HEAD"
    sha = clean.removeprefix(GITHUB + "/commit/")
    if clean.startswith(GITHUB + "/commit/") and COMMIT.fullmatch(sha):
        return sha
    raise ValueError("This adapter supports the Bioregistry GitHub repository and paper.")


def _windows_curl() -> Path | None:
    if os.name == "nt" and (root := os.environ.get("SystemRoot")):
        path = Path(root) / "System32" / "curl.exe"
        if path.is_file():
            return path
    return None


def fetch(url: str, limit: int, *, accept: str = "application/vnd.github+json") -> bytes:
    try:
        if curl := _windows_curl():
            # Windows' system curl provides a whole-transfer deadline. On EVO-X3
            # a Python socket timeout allowed the data response to stall past it.
            result = subprocess.run(
                [str(curl), "-q", "--fail", "--silent", "--show-error", "--connect-timeout", "8",
                 "--max-time", "25", "--max-filesize", str(limit), "--proto", "=https",
                 "--compressed", "--user-agent", "RNAHarness-ToolImport/1", "--header",
                 f"Accept: {accept}", url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            raw = result.stdout
        else:
            raw = _fetch_urllib(url, limit, accept)
    except (OSError, EOFError, http.client.HTTPException, subprocess.SubprocessError) as exc:
        raise SourceUnavailable("Could not retrieve the pinned Bioregistry source from GitHub.") from exc
    if len(raw) > limit:
        raise ValueError("Bioregistry source exceeds the supported size.")
    return raw


def _fetch_urllib(url: str, limit: int, accept: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "RNAHarness-ToolImport/1",
                                               "Accept-Encoding": "gzip", "Accept": accept})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
        if response.headers.get("Content-Encoding") == "gzip":
            with gzip.GzipFile(fileobj=response) as decompressed:
                return decompressed.read(limit + 1)
        return response.read(limit + 1)


def build_snapshot(url: str) -> tuple[str, str, dict[str, str]]:
    sha = source_ref(url)
    if sha == "HEAD":
        meta = json.loads(fetch(f"{API}/commits/HEAD", 256 * 1024))
        sha = meta.get("sha") if isinstance(meta, dict) else None
    if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
        raise ValueError("GitHub did not return an immutable commit SHA.")
    url = f"{API}/contents/src/bioregistry/data/bioregistry.json?ref={sha}"
    try:
        raw = fetch(url, MAX_SOURCE_BYTES, accept="application/vnd.github.raw+json")
    except SourceUnavailable:
        mirror = f"https://raw.githubusercontent.com/{REPO}/{sha}/src/bioregistry/data/bioregistry.json"
        raw = fetch(mirror, MAX_SOURCE_BYTES)
    data = json.loads(raw)
    return sha, hashlib.sha256(raw).hexdigest(), prefix_map(data)


def prefix_map(data: object) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError("Expected a Bioregistry object keyed by canonical prefix.")
    canonical = {key for key, entry in data.items()
                 if PREFIX.fullmatch(key) and isinstance(entry, dict)}
    aliases: dict[str, set[str]] = {}
    for key in sorted(canonical):
        synonyms = data[key].get("synonyms")
        if isinstance(synonyms, list):
            for alias in synonyms:
                if isinstance(alias, str) and PREFIX.fullmatch(clean := alias.casefold()):
                    aliases.setdefault(clean, set()).add(key)
    return {**{key: key for key in sorted(canonical)},
            **{alias: next(iter(matches)) for alias, matches in sorted(aliases.items())
               if len(matches) == 1 and alias not in canonical}}
