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
| `PLAN.md` | full design notes for the harness |
| `SKILL.md`, `scripts/lab.py`, `references/`, `examples/` | existing Claude Code skill for the lab API |
| `rnaharness/` | Python package: lab client, local workers, entity/evidence stores, Occamy kernel and MCP servers |
| `rnaharness/servers/lab_mcp.py` | P0 MCP server: 3 generic + 6 typed RNA tools |
| `skills/rnaharness-research/` | shared research skill for Claude Code and Codex |
| `gateway/` | TypeScript gateway: sessions, ack/progress, HTTP + SSE |
| `scripts/` | entry points: lab CLI, entity/evidence builders, research stack, Occamy kernel |
| `config/` | example OpenHarness MCP config |
| `docs/` | build and setup guides (`BUILD.zh-CN.md`) |
| `tests/` | Python tests for the lab API, local workers, entity/evidence and Occamy kernel |
| `_refs/` | optional local checkout of OpenHarness / BioClaw source, git-ignored |

## Quick start

For a fresh Windows checkout with local demo data and a standalone ViennaRNA
benchmark, start with [从仓库构建 RNAHarness](docs/BUILD.zh-CN.md). That guide
also wires the WeChat and QQ channels through the OpenClaw plugins.

Python (MCP server):

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]" --index-url https://pypi.org/simple
export ROMANCOHORT_API_KEY='rc_lab_...'
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m rnaharness.servers.lab_mcp
```

Add the `research` extra (`-e ".[dev,research]"`) for ViennaRNA and the pinned
`mcp` version used by the reference-hairpin tool.

Gateway:

```bash
cd gateway && npm ci && npm run dev
cd gateway && npm test
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

### Administrator-approved tool imports

Set `RNAHARNESS_TOOL_IMPORT_DB` to the same absolute SQLite path for the Occamy kernel, `rnaharness.servers.local_mcp`, and the operator. Keep this file in a directory writable only by the RNAHarness service identity and authorized operators. The database is disabled if its environment variable is unset.

Paste the [Bioregistry GitHub repository](https://github.com/biopragmatics/bioregistry) or [paper](https://doi.org/10.1038/s41597-022-01807-3) into Occamy. MCP clients can call `request_tool_import` with `url`. The request returns an ID that `tool_import_status` can check. RNAHarness pins the commit and builds a data-only mapping snapshot for review. Submitting the repository link again checks its current version.

An operator can open the approval window with `python -m rnaharness.tool_imports review`, or inspect `pending` and run `python -m rnaharness.tool_imports approve REQUEST_ID PLAN_SHA256`. A successful approval publishes the tested snapshot directly. Occamy and subsequent MCP `tools/list` calls then expose `imported_bioregistry_prefix`. For example, ask Occamy to normalize the database prefix `CHEBIID`.

The approval record contains the commit and source and snapshot digests. A lookup returns a `run_id`, the adapter version, and the snapshot commit. `python -m rnaharness.tool_imports replay RUN_ID` reruns that lookup on the archived snapshot and reports whether the mapping matches.

### Claude Code and Codex research skill

Copy the same [research skill](skills/rnaharness-research/SKILL.md) into `.claude/skills/rnaharness-research/` for Claude Code or `.agents/skills/rnaharness-research/` for Codex in your research project. Register its MCP server once, then invoke `/rnaharness-research` in Claude Code or `$rnaharness-research` in Codex. [Claude Code skill locations](https://code.claude.com/docs/en/skills#choose-where-skills-load) · [Codex skill locations](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)

Run the stdio server on a machine with RNAHarness installed. It offers database prefix normalization from the approved registry, human gene and mature miRNA metadata, indexed PMID lookup, and a fixed synthetic hairpin comparison. The lookup databases and the loopback ViennaRNA worker are supplied by the RNAHarness host. In the PowerShell example, set these paths to the interpreter and databases on that host.

```powershell
$rnaRepo = 'C:/RNAHarness'
$rnaPython = 'C:/RNAHarness/.venv/Scripts/python.exe'
$rnaEntityDb = 'C:/RNAHarness/data/entities.sqlite'
$rnaEvidenceDb = 'C:/RNAHarness/data/evidence.sqlite'
$rnaImportDb = 'C:/RNAHarness/data/tool-imports/registry.sqlite'

$rnaEnv = @(
  "PYTHONPATH=$rnaRepo"
  "RNAHARNESS_ENTITY_DB=$rnaEntityDb"
  "RNAHARNESS_EVIDENCE_DB=$rnaEvidenceDb"
  "RNAHARNESS_TOOL_IMPORT_DB=$rnaImportDb"
)
$rnaCommand = @('--', $rnaPython, '-m', 'rnaharness.servers.research_mcp')

claude mcp add --scope local --transport stdio rnaharness-research --env @rnaEnv @rnaCommand

$codexEnv = foreach ($setting in $rnaEnv) { '--env'; $setting }
codex mcp add rnaharness-research @codexEnv @rnaCommand
```

For researchers working on another machine, the operator can supply a dedicated SSH alias with a server-side forced command that runs only this stdio MCP server. Set the database paths on the host before launching the server. With an operator-provisioned alias such as `rnaharness-team`, the client registration uses SSH as the stdio command. [Claude Code MCP setup](https://code.claude.com/docs/en/mcp) · [Codex MCP setup](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

```powershell
$rnaConnection = @('--', 'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'rnaharness-team')
claude mcp add --scope local --transport stdio rnaharness-research @rnaConnection
codex mcp add rnaharness-research @rnaConnection
```
