---
name: rnaharness-research
description: Use the RNAHarness researcher MCP server for biomedical database prefix normalization, human gene and mature miRNA metadata, local PMID index checks, and a fixed synthetic RNA hairpin folding comparison.
---

# RNAHarness Research

Use the tools from the configured `rnaharness-research` MCP server. If this server is missing, report the missing connection and use the repository's **Claude Code and Codex research skill** setup section. Use this server's advertised tools for RNAHarness calls.

| Task | MCP tool |
| --- | --- |
| Resolve an alias such as `PMID` to its database prefix | `normalize_prefix` |
| Confirm a human gene symbol, alias, or database cross-reference | `resolve_human_gene` |
| Confirm a human mature miRNA accession, species, or arm | `resolve_human_mirna` |
| Check whether a PMID is present in the local index | `indexed_citation` |
| Compare the fixed `WT`, `L`, `R`, and `LR` synthetic hairpins | `fold_reference_hairpin` |

Use the returned IDs, snapshot, version, and coverage in your answer. A prefix mapping establishes the namespace. An entity match establishes identity. A PMID match establishes that the item is indexed. Claims about a relationship need their own supporting evidence. Report an unresolved match or a missing snapshot explicitly.

For a hairpin comparison, run each requested variant at its specified temperature. The available temperatures are 37 and 55 °C. Use the returned dot-bracket structure, MFE in kcal/mol, and `job_id`. Among the seven expected stem pairs `(1,20)` through `(7,14)`, count how many each MFE structure contains. Report restoration of the pairing pattern and recovery of MFE separately. Describe these as predictions from the same model and parameter settings.

Example request: Compare `WT` and `LR` at 55 °C and report the two job IDs, structures, and MFE difference.
