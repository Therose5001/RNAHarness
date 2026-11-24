from __future__ import annotations

import asyncio
import json
from pathlib import Path
import runpy

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_generated_stack_uses_only_the_research_server(tmp_path):
    scope = runpy.run_path(str(ROOT / "scripts" / "research_stack.py"))
    paths = scope["initialize"](tmp_path)
    config_path = Path(paths["config"])
    scope["enable_channels"](config_path, True, ["DEMO_OPENID"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["gateway"]["bind"] == "loopback"
    assert config["channels"]["openclaw-weixin"]["enabled"]
    assert config["channels"]["qqbot"]["allowFrom"] == ["DEMO_OPENID"]
    assert config["channels"]["qqbot"]["groupPolicy"] == "disabled"
    assert config["tools"]["allow"] == ["bundle-mcp"]
    with pytest.raises(ValueError, match="explicit"):
        scope["enable_channels"](config_path, True, ["*"])
    with pytest.raises(ValueError, match="uppercase"):
        scope["enable_channels"](config_path, True, ["demo-openid"])
    assert scope["initialize"](tmp_path) == paths
    assert json.loads(config_path.read_text(encoding="utf-8")) == config


def test_fresh_stack_stdio_fold_and_receipt(tmp_path):
    pytest.importorskip("RNA")
    scope = runpy.run_path(str(ROOT / "scripts" / "research_stack.py"))
    paths = scope["initialize"](tmp_path)
    config = json.loads(Path(paths["config"]).read_text(encoding="utf-8"))
    server = config["mcp"]["servers"]["rnaharness-research"]
    launch = StdioServerParameters(command=server["command"], args=server["args"],
                                   cwd=server["cwd"], env=server["env"])

    async def check():
        async with stdio_client(launch) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                assert {item.name for item in (await client.list_tools()).tools} == set(
                    server["toolFilter"]["include"]
                )
                job = await client.call_tool("fold_reference_hairpin", {
                    "variant": "LR", "temperature_c": 55,
                })
                assert not job.isError
                result = job.structuredContent or json.loads(job.content[0].text)
                assert result["ok"]
                data = result["data"]
                assert data["mfe"] == pytest.approx(-5.56, abs=0.01)
                assert data["structure"] == "(((((((......)))))))"
                receipt = tmp_path / "jobs" / (data["job_id"] + ".json")
                assert json.loads(receipt.read_text(encoding="utf-8"))["data"] == data

    asyncio.run(asyncio.wait_for(check(), 20))
