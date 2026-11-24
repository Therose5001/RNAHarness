from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from rnaharness import tool_import_source as source
from rnaharness import tool_imports as imports

RECORDS = {
    "chebi": {"synonyms": ["CHEBIID", "ambiguous", "go", "invalid alias"]},
    "go": {"synonyms": ["GOID", "ambiguous"]},
}
FIRST = "1" * 40
SECOND = "2" * 40


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "imports.sqlite"
    monkeypatch.setenv(imports.SETTING, str(db))
    return imports.ToolImports(db)


def prepare(monkeypatch, store, commit=FIRST, records=RECORDS):
    def fetch(url, limit, **headers):
        if url.endswith("/commits/HEAD"):
            return json.dumps({"sha": commit}).encode()
        assert url.endswith(f"/bioregistry.json?ref={commit}")
        assert headers["accept"] == "application/vnd.github.raw+json"
        return json.dumps(records).encode()

    monkeypatch.setattr(source, "fetch", fetch)
    return store.submit("https://doi.org/10.1038/s41597-022-01807-3")


def approve(store, proposal):
    return store.approve(proposal["request_id"], proposal["plan_sha256"])


def test_approved_update_and_replay(store, monkeypatch):
    first = prepare(monkeypatch, store)
    assert first["plan"]["commit"] == FIRST
    assert prepare(monkeypatch, store)["request_id"] == first["request_id"]
    assert [x["name"] for x in imports.tool_specs()] == [imports.REQUEST, imports.STATUS]
    assert not imports.call_import_tool(imports.TOOL, {"prefix": "CHEBIID"})["ok"]
    with pytest.raises(ValueError, match="Approval must match"):
        store.approve(first["request_id"], "0" * 64)
    assert approve(store, first)["status"] == "published"
    assert [event["kind"] for event in approve(store, first)["events"]] == [
        "import.requested", "import.approved", "import.verified", "import.published",
    ]
    original = store.lookup("GOID")
    assert original["data"]["canonical_prefix"] == "go"
    assert not store.lookup("ambiguous")["data"]["resolved"]
    assert store.lookup("go")["data"]["canonical_prefix"] == "go"

    second = prepare(monkeypatch, store, SECOND, {**RECORDS, "go": {}})
    assert second["plan"]["previous_commit"] == FIRST
    assert store.lookup("GOID")["provenance"]["snapshot"] == FIRST
    assert approve(store, second)["status"] == "published"
    current = store.lookup("GOID")
    assert current["provenance"]["snapshot"] == SECOND
    assert current["data"]["canonical_prefix"] is None
    replay = store.replay(original["data"]["run_id"])
    assert replay["comparison"] == {"matches": True}
    assert replay["provenance"]["snapshot"] == FIRST
    assert replay["data"]["canonical_prefix"] == "go"
    assert replay["data"]["run_id"] != original["data"]["run_id"]


def test_failed_and_stale_updates_do_not_activate(store, monkeypatch):
    first = prepare(monkeypatch, store)
    stale = prepare(monkeypatch, store, SECOND)
    approve(store, first)
    with pytest.raises(ValueError, match="active version has changed"):
        approve(store, stale)
    broken = prepare(monkeypatch, store, SECOND, {"go": {}})
    failed = approve(store, broken)
    assert failed["status"] == "failed"
    assert failed["events"][-1]["kind"] == "import.failed"
    assert store.lookup("CHEBIID")["provenance"]["snapshot"] == FIRST


def test_source_policy_and_approval_are_not_agent_tools(store, monkeypatch):
    def unexpected_fetch(*args):
        pytest.fail("Unrecognized URL triggered a fetch")

    monkeypatch.setattr(source, "fetch", unexpected_fetch)
    for url in ("https://github.com/biopragmatics/bioregistry/../../elsewhere",
                "https://github.com/biopragmatics/bioregistry?next=https://example.org",
                "https://github.com.example.org/biopragmatics/bioregistry"):
        assert not imports.call_import_tool(imports.REQUEST, {"url": url})["ok"]
    assert not imports.is_import_tool("approve")
    assert not imports.call_import_tool(imports.REQUEST, {"url": source.GITHUB, "approve": True})["ok"]
    assert source.source_ref(source.GITHUB + "/commit/" + FIRST) == FIRST


def test_github_read_timeout_returns_actionable_error(store, monkeypatch):
    class SlowSource:
        headers = {}

        def open(self, *_args, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            raise TimeoutError("read timed out")

    monkeypatch.setattr(source, "_windows_curl", lambda: None)
    monkeypatch.setattr(source.urllib.request, "build_opener", lambda *_: SlowSource())
    result = imports.call_import_tool(imports.REQUEST, {"url": source.GITHUB})
    assert not result["ok"] and "Could not retrieve" in result["error"]["message"]
    assert not store.pending()


def test_pinned_github_raw_endpoint_on_contents_api_failure(monkeypatch):
    calls = []

    def fetch(url, limit, **headers):
        calls.append(url)
        if len(calls) == 1:
            raise source.SourceUnavailable("Contents API timed out")
        return json.dumps(RECORDS).encode()

    monkeypatch.setattr(source, "fetch", fetch)
    commit, _, mapping = source.build_snapshot(source.GITHUB + "/commit/" + FIRST)
    assert commit == FIRST and mapping["chebiid"] == "chebi"
    assert calls[0].endswith(f"?ref={FIRST}")
    assert calls[1].startswith(f"https://raw.githubusercontent.com/{source.REPO}/{FIRST}/")


def test_mcp_stdio_observes_publication_without_restart(store, monkeypatch):
    proposal = prepare(monkeypatch, store)
    root = Path(__file__).resolve().parents[1]
    config = StdioServerParameters(command=sys.executable, args=["-m", "rnaharness.servers.local_mcp"],
                                   cwd=root, env={imports.SETTING: str(store.path)})

    async def check():
        async with stdio_client(config) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                before = {tool.name for tool in (await session.list_tools()).tools}
                assert imports.REQUEST in before and imports.TOOL not in before
                approve(store, proposal)
                after = {tool.name for tool in (await session.list_tools()).tools}
                assert imports.TOOL in after
                result = await session.call_tool(imports.TOOL, {"prefix": "CHEBIID"})
                assert not result.isError
                payload = result.structuredContent or json.loads(result.content[0].text)
                assert payload["data"]["canonical_prefix"] == "chebi"

    asyncio.run(asyncio.wait_for(check(), 20))
