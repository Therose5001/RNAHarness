from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from rnaharness import envelope as env
from rnaharness import tool_imports
from rnaharness.entity import EntityStore
from rnaharness.entity.loaders import load_hgnc, load_mirbase
from rnaharness.evidence.store import EvidenceStore
from rnaharness.servers import research_mcp
from rnaharness.tool_import_source import GITHUB

FIXTURES = Path(__file__).parent / "fixtures"


def test_mcp_discovery_and_queries(tmp_path, monkeypatch):
    entities = tmp_path / "entities.sqlite"
    evidence_db = tmp_path / "evidence.sqlite"
    imports_db = tmp_path / "imports.sqlite"
    entity = EntityStore.open(entities, create=True)
    load_hgnc(entity, FIXTURES / "hgnc_mini.tsv", "fixture")
    load_mirbase(entity, FIXTURES / "mature_mini.fa", snapshot="fixture")
    entity.commit()
    entity.close()

    evidence = EvidenceStore.open(evidence_db, create=True)
    evidence.upsert_document({"doc_id": "fixture", "pmid": "17437991", "title": "Recorded title"})
    evidence.set_meta("snapshot:pmc", "fixture")
    evidence.commit()
    evidence.close()

    monkeypatch.setattr(tool_imports, "build_snapshot", lambda _: (
        "1" * 40, "2" * 64, {"chebi": "chebi", "chebiid": "chebi", "go": "go"},
    ))
    imports = tool_imports.ToolImports(imports_db)
    request = imports.submit(GITHUB)
    assert imports.approve(request["request_id"], request["plan_sha256"])["status"] == "published"
    config = StdioServerParameters(
        command=sys.executable, args=["-m", "rnaharness.servers.research_mcp"],
        cwd=FIXTURES.parents[1], env={"RNAHARNESS_ENTITY_DB": str(entities),
            "RNAHARNESS_EVIDENCE_DB": str(evidence_db), tool_imports.SETTING: str(imports_db)},
    )

    async def check():
        async with stdio_client(config) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                assert set(tools) == {"normalize_prefix", "resolve_human_gene", "resolve_human_mirna",
                                      "indexed_citation", "fold_reference_hairpin"}
                assert set(tools["fold_reference_hairpin"].inputSchema["properties"]) == {
                    "variant", "temperature_c",
                }

                async def call(name, args):
                    result = await client.call_tool(name, args)
                    assert not result.isError
                    return result.structuredContent or json.loads(result.content[0].text)

                prefix = await call("normalize_prefix", {"prefix": "CHEBIID"})
                assert prefix["data"]["canonical_prefix"] == "chebi"
                assert prefix["data"]["run_id"]
                gene = await call("resolve_human_gene", {"symbol": "PTEN"})
                assert gene["data"]["matches"][0]["id"] == "HGNC:9588"
                mirna = await call("resolve_human_mirna", {"name": "hsa-miR-21-5p"})
                match = mirna["data"]["matches"][0]
                assert (match["id"], match["arm"]) == ("MIMAT0000076", "5p")
                assert "sequence" not in match and "seed_7mer" not in match
                citation = await call("indexed_citation", {"pmid": "17437991"})
                assert citation["data"]["citation"]["title"] == "Recorded title"
                absent = await call("indexed_citation", {"pmid": "18292307"})
                assert absent["error"]["code"] == env.UNRESOLVED
                assert absent["error"]["detail"]["coverage"]["documents"] == 1

    asyncio.run(asyncio.wait_for(check(), 20))


def test_fold_parameters_and_compact_result(monkeypatch):
    def run(worker, args):
        assert worker == "vienna"
        assert args == {"tool": "fold", "params": {
            "sequence": research_mcp._HAIRPINS["LR"], "temperature": 55, "dangles": 2,
        }}
        return env.ok({"job_id": "test-job", "result": {
            "structure": "(((((((......)))))))", "mfe": -5.56, "svg": "large", "bpp": [1],
        }}, env.provenance("lab.adapter"))

    monkeypatch.setattr(research_mcp.adapter, "run_tool", run)
    assert not research_mcp.fold_reference_hairpin("LR", 45)["ok"]
    assert not research_mcp.fold_reference_hairpin("other", 55)["ok"]
    reply = research_mcp.fold_reference_hairpin("LR", 55)["data"]
    assert reply["job_id"] == "test-job" and reply["mfe"] == -5.56
    assert "svg" not in reply and "bpp" not in reply
