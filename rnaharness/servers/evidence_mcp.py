"""evidence_mcp — literature evidence as MCP tools (PLAN.md §6).

Three tools: literature_search, evidence_lookup, citation_verify. They read
the local evidence index (`RNAHARNESS_EVIDENCE_DB`) and resolve names through
the entity store (`RNAHARNESS_ENTITY_DB`).

Run:  python -m rnaharness.servers.evidence_mcp
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..evidence import search as evidence

server = FastMCP("evidence_mcp", log_level="WARNING", instructions=(
    "Local literature evidence. Candidate passages are retrieval leads; only "
    "structured relation claims that pass identity, metadata, source, and "
    "passage checks are returned as evidence. A miss describes coverage, "
    "never biological absence. Every result is "
    "{ok,data,error,provenance,evidence}."
))


@server.tool(name="literature_search", description=(
    "Retrieve boundary-checked passages and structured relation claims for a "
    "miRNA-gene pair. candidate_passages are leads; evidence contains only "
    "relations that pass canonical identity, species/tax, arm, document, and "
    "passage-attribution checks. "
    "Preserves confidence, polarity, curation, extraction and source metadata. "
    "A miss is limited-corpus coverage, not proof of absence."
))
def tool_literature_search(mirna: str, gene: str, species: str = "hsa",
                           top_k: int = 10) -> dict:
    return evidence.literature_search(mirna, gene, species, top_k)


@server.tool(name="evidence_lookup", description=(
    "Look up structured relation records for an unambiguous miRNA-gene pair. "
    "Returns canonical candidates, species/tax ID, arm, polarity, method, "
    "experimental_method, confidence, curation_status, extraction_method and "
    "extracted_by. Relations with unresolved endpoints or conflicting species, "
    "taxonomy, arm, document, or source attribution are excluded with a reason."
))
def tool_evidence_lookup(mirna: str, gene: str, species: str = "hsa") -> dict:
    return evidence.evidence_lookup(mirna, gene, species)


@server.tool(name="citation_verify", description=(
    "Separate exact quotation from scientific claim support in an indexed "
    "document. quote_match reports a passage-relative exact span and source "
    "hash; claim_support is not_assessed without reviewed annotation. Token "
    "overlap and min_overlap rank candidates only. Legacy verified is false by "
    "default and deprecated: exact quotation alone is insufficient."
))
def tool_citation_verify(pmid_or_doc_id: str, claim: str,
                         min_overlap: float = 0.8) -> dict:
    return evidence.citation_verify(pmid_or_doc_id, claim, min_overlap)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
