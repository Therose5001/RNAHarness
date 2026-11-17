# RNAHarness

Agent harness for RNA research. The harness is the centre; the model is a tool.

```
channels → gateway → OpenHarness kernel → MCP tools → services
```

See [PLAN.md](PLAN.md) for the full design. This repo is also still a Claude
Code skill (`SKILL.md` + `scripts/lab.py`) — that path is unchanged.

## Layout

| path | what |
|---|---|
| `SKILL.md`, `scripts/lab.py`, `references/`, `examples/` | existing Claude Code skill for the lab API |
| `rnaharness/` | Python package: lab client, local workers, entity/evidence stores, Occamy kernel and MCP servers |
| `rnaharness/servers/lab_mcp.py` | P0 MCP server: 3 generic + 6 typed RNA tools |
| `gateway/` | TypeScript gateway: sessions, ack/progress, HTTP + SSE |
| `config/` | example OpenHarness MCP config |
| `tests/` | Python tests for the lab API, local workers, entity/evidence and Occamy kernel |
| `_refs/` | vendored OpenHarness / BioClaw source for reference |

## Quick start

Python (MCP server):

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]" --index-url https://pypi.org/simple
export ROMANCOHORT_API_KEY='rc_lab_...'
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m rnaharness.servers.lab_mcp
```

Gateway:

```bash
cd gateway && npm install && npm run dev
```

## Skill CLI (unchanged)

```text
lab.py tools [--json]          lab.py status RUN_ID
lab.py health                  lab.py logs RUN_ID
lab.py run --tool TOOL --params params.json [--wait] [--download]
lab.py files RUN_ID [--hash]   lab.py download RUN_ID --out runs
lab.py cancel RUN_ID           lab.py usage
lab.py samples --tool ncbp     lab.py models|datasets --tool vaxseer
lab.py lnpdb-search --query siRNA --limit 20
lab.py lnpdb-download --out LNPDB.csv
```

The key is sent as `Authorization: Bearer ...`; it is never printed or
stored in the output directory. No third-party Python packages are required
for the CLI.

## Status (P0)

- [x] shared lab HTTP client + unified `{ok,data,error,provenance,evidence}` envelope
- [x] `lab_mcp`: `lab_run` / `lab_status` / `lab_files` + vienna / rhofold / ncbp / gatsite / gemorna / rnadvisor
- [x] mock-backend tests (409 reuse, 429/401/5xx mapping, sha256 verification, schema rejection)
- [x] gateway skeleton: HTTP channel, session mapping, ack, SSE progress, OpenHarness one-shot kernel
- [ ] install & verify OpenHarness + MCP end to end
- [x] P1 entity layer (`local_mcp`) and P2 evidence layer
- [x] Local OpenAI-compatible Occamy kernel with dynamic worker discovery and exact RNA sequence references
- [x] Local worker adapter, ViennaRNA folding, RhoFold+ PDB artifacts, entity/evidence lookup and provenance-aware tools


## Local Occamy kernel

The gateway can run the bundled Occamy kernel instead of the `oh` CLI by setting `OPENHARNESS_CMD` to a Python executable and `OPENHARNESS_ARGS` to a JSON argument array containing `scripts/occamy_kernel.py`. Configure `OCCAMY_BASE` and `OCCAMY_MODEL` for the local OpenAI-compatible inference server. The kernel discovers available workers from their OpenAPI contracts and uses loopback endpoints. RhoFold PDB artifacts are surfaced only after a worker confirms a completed job.
