"""Entity-aware evidence retrieval over the local evidence store.

The public tools deliberately keep three questions separate:

* what text was retrieved (``candidate_passages``);
* what structured relation claims passed canonical identity, metadata,
  source, and passage checks (``evidence``); and
* whether an exact quotation proves that a claim is scientifically supported.

An empty result describes this limited index. It never means that a biological
relationship does not exist.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Iterable
from typing import Any

from .. import envelope as env
from ..entity import EntityStore, NotLoaded, mirna_candidates
from ..entity.naming import gene_candidates, parse_mirna_name
from .store import EvidenceStore

SOURCE = "evidence"
ENTITY_ENV = "RNAHARNESS_ENTITY_DB"
SCHEMA_VERSION = 2

SPECIES = {
    "hsa": "9606",
    "mmu": "10090",
    "rno": "10116",
    "dme": "7227",
    "cel": "6239",
    "dre": "7955",
}
SPECIES_ALIASES = {
    "human": "hsa",
    "homo sapiens": "hsa",
    "9606": "hsa",
    "mouse": "mmu",
    "mice": "mmu",
    "mus musculus": "mmu",
    "10090": "mmu",
    "rat": "rno",
    "rattus norvegicus": "rno",
    "10116": "rno",
    "fly": "dme",
    "drosophila melanogaster": "dme",
    "7227": "dme",
    "worm": "cel",
    "caenorhabditis elegans": "cel",
    "6239": "cel",
    "zebrafish": "dre",
    "danio rerio": "dre",
    "7955": "dre",
}


def _species_info(value: str | None) -> dict:
    raw = (value or "").strip()
    key = raw.lower()
    code = key if key in SPECIES else SPECIES_ALIASES.get(key)
    return {
        "input": raw or None,
        "code": code,
        "tax_id": SPECIES.get(code) if code else None,
        "known": bool(code),
    }


def _entity_species(row: dict) -> dict:
    species = (row.get("species") or "").strip().lower() or None
    tax_id = str(row.get("taxid") or "").strip() or None
    if species in SPECIES:
        return {"code": species, "tax_id": SPECIES[species]}
    if species in SPECIES_ALIASES:
        code = SPECIES_ALIASES[species]
        return {"code": code, "tax_id": SPECIES[code]}
    if tax_id:
        code = next((c for c, t in SPECIES.items() if t == tax_id), None)
        if code:
            return {"code": code, "tax_id": tax_id}
        return {"code": None, "tax_id": tax_id}
    parsed = parse_mirna_name(row.get("name") or "")
    code = parsed.get("species")
    if code in SPECIES:
        return {"code": code, "tax_id": SPECIES[code]}
    return {"code": None, "tax_id": None}


def _matches_requested_species(row: dict, requested: dict) -> bool:
    if not requested["known"]:
        return True
    actual = _entity_species(row)
    if actual.get("tax_id") and requested.get("tax_id"):
        return actual["tax_id"] == requested["tax_id"]
    return actual.get("code") == requested.get("code")


def _entity_candidate_record(row: dict) -> dict:
    parsed = parse_mirna_name(row.get("name") or "")
    species = _entity_species(row)
    return {
        "canonical_id": row.get("canonical_id"),
        "name": row.get("name"),
        "species": species.get("code"),
        "tax_id": species.get("tax_id"),
        "arm": parsed.get("arm"),
    }


def _dedupe_rows(rows: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        canonical_id = str(row.get("canonical_id") or "")
        if canonical_id and canonical_id not in seen:
            seen.add(canonical_id)
            out.append(row)
    return out


def _endpoint_unresolved(kind: str, reason: str, **extra: Any) -> dict:
    return {
        "kind": kind,
        "status": "unresolved",
        "species_status": extra.pop("species_status", "unknown"),
        "tax_id": extra.pop("tax_id", None),
        "arm": extra.pop("arm", None),
        "arm_status": extra.pop("arm_status", "unknown"),
        "canonical_ids": [],
        "canonical_candidates": [],
        "reason": reason,
        **extra,
    }

def _resolve_mirna(name: str, requested: dict, store: EntityStore) -> dict:
    parsed = parse_mirna_name(name)
    terms = mirna_candidates(name)
    if parsed.get("family") and not parsed.get("explicit_species") and requested.get("code"):
        terms += mirna_candidates(f"{requested['code']}-{name}")
    rows = _dedupe_rows(store.by_alias(_dedupe(terms), kind="mirna"))

    explicit_species = parsed.get("species")
    if (explicit_species and requested["known"]
            and explicit_species != requested["code"]):
        return _endpoint_unresolved(
            "mirna", "explicit_species_conflict", species_status="conflict",
            arm=parsed.get("arm"), arm_status="not_checked",
            explicit_species=explicit_species,
            requested_species=requested["code"])

    species_rows = [r for r in rows if _matches_requested_species(r, requested)]
    if rows and requested["known"] and not species_rows:
        return _endpoint_unresolved(
            "mirna", "species_conflict", species_status="conflict",
            arm=parsed.get("arm"), arm_status="not_checked",
            available_candidates=[_entity_candidate_record(r) for r in rows],
            requested_species=requested["code"])
    if not species_rows:
        return _endpoint_unresolved(
            "mirna", "not_found", arm=parsed.get("arm"),
            arm_status="unknown")

    arm = parsed.get("arm")
    if arm:
        arm_rows = [
            r for r in species_rows
            if parse_mirna_name(r.get("name") or "").get("arm") == arm
        ]
        if not arm_rows:
            return _endpoint_unresolved(
                "mirna", "arm_conflict", species_status="matched",
                arm=arm, arm_status="conflict",
                available_candidates=[_entity_candidate_record(r) for r in species_rows])
        species_rows = arm_rows
        arm_status = "matched"
    else:
        arm_status = "unspecified"

    candidates = [_entity_candidate_record(r) for r in species_rows]
    species_status = "matched" if requested["known"] else "unknown"
    if len(candidates) == 1:
        candidate = candidates[0]
        return {
            "kind": "mirna",
            "status": "resolved",
            "species_status": species_status,
            "tax_id": candidate.get("tax_id"),
            "arm": candidate.get("arm"),
            "arm_status": arm_status,
            "canonical_ids": [candidate["canonical_id"]],
            "canonical_candidates": candidates,
            "reason": "unique_entity",
        }
    return {
        "kind": "mirna",
        "status": "ambiguous",
        "species_status": species_status,
        "tax_id": candidates[0].get("tax_id") if candidates else None,
        "arm": arm,
        "arm_status": arm_status,
        "canonical_ids": [],
        "canonical_candidates": candidates,
        "reason": "multiple_canonical_entities",
    }


def _resolve_gene(name: str, requested: dict, store: EntityStore) -> dict:
    rows = _dedupe_rows(store.by_alias(gene_candidates(name), kind="gene"))
    species_rows = [r for r in rows if _matches_requested_species(r, requested)]
    if rows and requested["known"] and not species_rows:
        return _endpoint_unresolved(
            "gene", "species_conflict", species_status="conflict",
            available_candidates=[_entity_candidate_record(r) for r in rows],
            requested_species=requested["code"])
    if not species_rows:
        return _endpoint_unresolved("gene", "not_found")
    candidates = [_entity_candidate_record(r) for r in species_rows]
    species_status = "matched" if requested["known"] else "unknown"
    if len(candidates) == 1:
        candidate = candidates[0]
        return {
            "kind": "gene",
            "status": "resolved",
            "species_status": species_status,
            "tax_id": candidate.get("tax_id"),
            "arm": None,
            "arm_status": "not_applicable",
            "canonical_ids": [candidate["canonical_id"]],
            "canonical_candidates": candidates,
            "reason": "unique_entity",
        }
    return {
        "kind": "gene",
        "status": "ambiguous",
        "species_status": species_status,
        "tax_id": candidates[0].get("tax_id") if candidates else None,
        "arm": None,
        "arm_status": "not_applicable",
        "canonical_ids": [],
        "canonical_candidates": candidates,
        "reason": "multiple_canonical_entities",
    }


def _overall_status(mirna_endpoint: dict, gene_endpoint: dict) -> str:
    endpoints = (mirna_endpoint, gene_endpoint)
    if any(
        endpoint.get("species_status") == "conflict"
        or endpoint.get("arm_status") == "conflict"
        or endpoint.get("status") == "ambiguous"
        for endpoint in endpoints
    ):
        return "ambiguous"
    if any(endpoint.get("status") != "resolved" for endpoint in endpoints):
        return "unresolved"
    return "resolved"


def resolve_pair(mirna: str, gene: str, species: str = "hsa") -> dict:
    """Resolve both endpoints and report species and arm applicability.

    ``resolved`` is true only when each endpoint has one unambiguous canonical
    identifier in the requested species. Candidate metadata remains available
    for ambiguity diagnostics, but evidence lookup never falls back to it.
    """
    requested = _species_info(species)
    base = {
        "query": {"mirna": mirna, "gene": gene, "species": species},
        "requested_species": requested,
        "status": "unresolved",
        "resolved": False,
        "reason": "",
        "mirna_ids": [],
        "gene_ids": [],
        "endpoints": {},
        "entity_sources": {},
    }
    entity_path = os.environ.get(ENTITY_ENV)
    try:
        store = EntityStore.open(entity_path) if entity_path else EntityStore.open()
    except NotLoaded:
        base["reason"] = "entity_store_not_loaded"
        base["endpoints"] = {
            "mirna": _endpoint_unresolved("mirna", "entity_store_not_loaded"),
            "gene": _endpoint_unresolved("gene", "entity_store_not_loaded"),
        }
        return base
    except Exception as exc:
        base["reason"] = f"entity_store_error: {exc}"
        base["endpoints"] = {
            "mirna": _endpoint_unresolved("mirna", base["reason"]),
            "gene": _endpoint_unresolved("gene", base["reason"]),
        }
        return base

    try:
        meta = store.meta()
        base["entity_sources"] = {
            key.split(":", 1)[1]: value
            for key, value in meta.items() if key.startswith("source:")
        }
        mirna_endpoint = _resolve_mirna(mirna, requested, store)
        gene_endpoint = _resolve_gene(gene, requested, store)
        base["endpoints"] = {"mirna": mirna_endpoint, "gene": gene_endpoint}
        base["status"] = _overall_status(mirna_endpoint, gene_endpoint)
        base["resolved"] = base["status"] == "resolved"
        base["mirna_ids"] = list(mirna_endpoint.get("canonical_ids") or [])
        base["gene_ids"] = list(gene_endpoint.get("canonical_ids") or [])
        if base["resolved"]:
            base["reason"] = "both_endpoints_resolved"
        elif base["status"] == "ambiguous":
            conflicts = [
                f"{kind}_{endpoint['reason']}"
                for kind, endpoint in base["endpoints"].items()
                if endpoint.get("species_status") == "conflict"
                or endpoint.get("arm_status") == "conflict"
                or endpoint.get("status") == "ambiguous"
            ]
            base["reason"] = ";".join(conflicts)
        else:
            missing = [
                f"{kind}_{endpoint['reason']}"
                for kind, endpoint in base["endpoints"].items()
                if endpoint.get("status") != "resolved"
            ]
            base["reason"] = ";".join(missing)
        return base
    finally:
        store.close()


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = (value or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out

def _surface_terms(name: str, endpoint: dict, *, mirna: bool) -> list[str]:
    terms = [name]
    if mirna:
        terms.extend(mirna_candidates(name))
        parsed = parse_mirna_name(name)
        if parsed.get("family") and not parsed.get("explicit_species"):
            requested = (endpoint.get("canonical_candidates") or [{}])[0]
            species = requested.get("species")
            if species:
                terms.extend(mirna_candidates(f"{species}-{name}"))
        terms.extend(
            candidate.get("name") or ""
            for candidate in endpoint.get("canonical_candidates", [])
        )
        if parsed.get("explicit_arm"):
            terms = [
                term for term in terms
                if parse_mirna_name(term).get("arm") == parsed.get("arm")
                or not parse_mirna_name(term).get("family")
            ]
    else:
        terms.extend(gene_candidates(name))
        terms.extend(
            candidate.get("name") or ""
            for candidate in endpoint.get("canonical_candidates", [])
        )
    return _dedupe(terms)


def _entity_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _entity_hit(text: str, terms: Iterable[str]) -> bool:
    return any(_entity_pattern(term).search(text or "") for term in terms if term)


def _source_hash(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stored_span(passage: dict | None) -> dict | None:
    if not passage or passage.get("char_start") is None or passage.get("char_end") is None:
        return None
    return {
        "span": [passage["char_start"], passage["char_end"]],
        "basis": "stored_normalized_document_offsets",
        "original_paper_offsets_claimed": False,
    }


def _snippet(text: str | None, terms: list[str], width: int = 320) -> str | None:
    if text is None:
        return None
    positions = []
    for term in terms:
        match = _entity_pattern(term).search(text)
        if match:
            positions.append(match.start())
    start = max(0, min(positions) - 60) if positions else 0
    snippet = text[start:start + width]
    if start:
        snippet = "…" + snippet
    if start + width < len(text):
        snippet += "…"
    return snippet


def _candidate_record(passage: dict, mirna_terms: list[str],
                      gene_terms: list[str]) -> dict:
    text = passage.get("text") or ""
    mirna_hit = _entity_hit(text, mirna_terms)
    gene_hit = _entity_hit(text, gene_terms)
    sides = []
    if mirna_hit:
        sides.append("mirna")
    if gene_hit:
        sides.append("gene")
    return {
        "passage_id": passage.get("passage_id"),
        "doc_id": passage.get("doc_id"),
        "pmid": passage.get("pmid"),
        "pmcid": passage.get("pmcid"),
        "section": passage.get("section"),
        "snippet": _snippet(text, mirna_terms + gene_terms),
        "matched_sides": sides,
        "pair_mentioned": mirna_hit and gene_hit,
        "stored_passage_span": _stored_span(passage),
        "source_text_hash": _source_hash(text),
        "snapshot": passage.get("snapshot"),
        "candidate_only": True,
    }


RELATION_CLAUSE_SPLIT = re.compile(
    r"(?:[.!?]+(?=\s|$)|;\s*|,\s*(?:while|whereas|but|although)\s+|\b(?:while|whereas)\b)",
    re.IGNORECASE,
)
RELATION_CUE = re.compile(
    r"\b(?:targets?|targeted|targeting|represses?|repressed|repression|"
    r"regulates?|regulated|binds?|bound|inhibits?|inhibited|silences?|"
    r"silenced|degrades?|degraded|cleaves?|cleaved|downregulates?|"
    r"down-regulated|upregulates?|up-regulated|suppresses?|suppressed|"
    r"suppression|reduces?|reduced|reducing)\b",
    re.IGNORECASE,
)


def _relation_species_status(rel: dict, requested: dict) -> tuple[str, str | None]:
    """Return relation metadata status without inferring missing identity."""
    species = _species_info(rel.get("species"))
    tax_id = str(rel.get("tax_id") or "").strip() or None
    tax_code = next((code for code, tax in SPECIES.items() if tax == tax_id), None)
    if species["input"] and not species["known"]:
        return "conflict", None
    if tax_id and not tax_code:
        return "conflict", None
    if species["known"] and tax_id and species["tax_id"] != tax_id:
        return "conflict", None
    code = species["code"] or tax_code
    if not code:
        return "missing", None
    if not requested.get("known") or code != requested.get("code"):
        return "conflict", code
    return "matched", code


def _relation_endpoint_rows(
    value: str | None,
    kind: str,
    entity_store: EntityStore,
    requested: dict,
) -> list[dict]:
    if not value:
        return []
    terms = mirna_candidates(value) if kind == "mirna" else gene_candidates(value)
    rows = _dedupe_rows(entity_store.by_alias(_dedupe(terms), kind=kind))
    rows = [row for row in rows if _matches_requested_species(row, requested)]
    if kind == "mirna":
        parsed = parse_mirna_name(value)
        if parsed.get("species") and parsed["species"] != requested.get("code"):
            return []
        if parsed.get("arm"):
            rows = [row for row in rows if
                    parse_mirna_name(row.get("name") or "").get("arm") == parsed["arm"]]
    return rows


def _endpoint_text_terms(
    value: str | None,
    rows: list[dict],
    entity_store: EntityStore,
) -> list[str]:
    terms = [value or ""]
    for row in rows:
        terms.extend([
            row.get("name") or "",
            row.get("canonical_id") or "",
        ])
        terms.extend(
            alias.get("alias") or ""
            for alias in entity_store.aliases_for(int(row["id"]))
        )
    return _dedupe(terms)


_MIRNA_MENTION = re.compile(
    r"(?<![A-Za-z0-9-])(?:[a-z]{3}-)?(?:mir|microrna)-?\d+[a-z]?"
    r"(?:-[35]p)?(?![A-Za-z0-9-])", re.IGNORECASE,
)
_SOURCE_SPECIES_MARKERS = {
    "hsa": re.compile(r"\b(?:human|homo\s+sapiens|hek293|hek-293|hela|hepg2)\b", re.I),
    "mmu": re.compile(r"\b(?:mouse|mice|murine|mus\s+musculus|nih3t3|3t3)\b", re.I),
    "rno": re.compile(r"\b(?:rat|rats|rattus\s+norvegicus)\b", re.I),
}


def _source_mirna_hit(text: str, terms: list[str], expected: dict) -> bool:
    """Do not match a broad alias inside an explicitly incompatible mention."""
    def mask_conflict(match: re.Match) -> str:
        parsed = parse_mirna_name(match.group())
        conflict = any(
            parsed.get(key) and expected.get(key) and parsed[key] != expected[key]
            for key in ("species", "arm")
        )
        return " " * len(match.group()) if conflict else match.group()
    return _entity_hit(_MIRNA_MENTION.sub(mask_conflict, text), terms)


def _source_attribution(
    text: str | None,
    mirna_terms: list[str],
    gene_terms: list[str],
    expected_mirna: dict,
) -> tuple[str, str]:
    """Require both endpoints and a relation cue in the same clause."""
    if text is None:
        return "source_attribution_missing", "missing"
    mirna_present = _source_mirna_hit(text, mirna_terms, expected_mirna)
    gene_present = _entity_hit(text, gene_terms)
    if not mirna_present:
        return "linked_passage_missing_mirna_endpoint", "missing_mirna"
    if not gene_present:
        return "linked_passage_missing_gene_endpoint", "missing_gene"
    for clause in RELATION_CLAUSE_SPLIT.split(text):
        species_markers = {code for code, pattern in _SOURCE_SPECIES_MARKERS.items()
                           if pattern.search(clause)}
        if species_markers and species_markers != {expected_mirna.get("species")}:
            continue
        if (
            _source_mirna_hit(clause, mirna_terms, expected_mirna)
            and _entity_hit(clause, gene_terms)
            and RELATION_CUE.search(clause)
        ):
            return "same_clause_endpoints_and_relation_cue", "attributed"
    return "source_attribution_missing", "missing_attribution"


def _relation_snapshot(
    store: EvidenceStore,
    rel: dict,
    passage: dict | None,
) -> str | None:
    if passage and passage.get("snapshot"):
        return passage["snapshot"]
    if rel.get("doc_id"):
        document = store.get_document(rel["doc_id"])
        if document and document.get("snapshot"):
            return document["snapshot"]
    return None


def _normalise_relation(rel: dict, passage: dict | None,
                        snapshot: str | None,
                        canonical_mirna_id: str,
                        canonical_gene_id: str,
                        source_attribution_status: str) -> dict:
    polarity = (rel.get("polarity") or "unknown").strip().lower()
    if polarity not in {"positive", "negative", "speculative", "unknown"}:
        polarity = "unknown"
    evidence_status = {
        "positive": "positive_relation_claim",
        "negative": "negative_relation_claim",
        "speculative": "speculative_relation_claim",
        "unknown": "unverified_relation_claim",
    }[polarity]
    text = passage.get("text") if passage else None
    record = {
        "relation_id": rel.get("relation_id"),
        "mirna_id": rel.get("mirna_id"),
        "gene_id": rel.get("gene_id"),
        "canonical_mirna_id": canonical_mirna_id,
        "canonical_gene_id": canonical_gene_id,
        "relation_type": rel.get("relation_type") or "unknown",
        "method": rel.get("method"),
        "experimental_method": rel.get("experimental_method"),
        "confidence": rel.get("confidence") or "unknown",
        "extraction_method": rel.get("extraction_method") or "unknown",
        "extracted_by": rel.get("extracted_by") or "unknown",
        "curation_status": rel.get("curation_status") or "unreviewed",
        "polarity": polarity,
        "species": rel.get("species") or None,
        "tax_id": str(rel.get("tax_id") or "") or None,
        "mirna_arm": rel.get("mirna_arm") or None,
        "doc_id": rel.get("doc_id"),
        "passage_id": rel.get("passage_id"),
        "pmid": passage.get("pmid") if passage else None,
        "pmcid": passage.get("pmcid") if passage else None,
        "section": passage.get("section") if passage else None,
        "snippet": _snippet(text, []) if passage else None,
        "stored_passage_span": _stored_span(passage),
        "source_text_hash": _source_hash(text),
        "snapshot": snapshot,
        "evidence_status": evidence_status,
        "source_attribution_status": source_attribution_status,
        "candidate_only": False,
    }
    if not passage and rel.get("passage_id"):
        record["source_status"] = "passage_missing"
    elif not passage:
        record["source_status"] = "no_passage_linked"
    else:
        record["source_status"] = "passage_indexed"
    return record


def _assess_relation(
    rel: dict,
    passage: dict | None,
    requested: dict,
    resolution: dict,
    entity_store: EntityStore,
    evidence_store: EvidenceStore,
) -> tuple[dict | None, dict | None]:
    snapshot = _relation_snapshot(evidence_store, rel, passage)

    def reject(reason: str) -> tuple[None, dict]:
        return None, {
            "relation_id": rel.get("relation_id"),
            "reason": reason,
            "snapshot": snapshot,
        }

    mirna_rows = _relation_endpoint_rows(
        rel.get("mirna_id"), "mirna", entity_store, requested)
    allowed_mirna = set(resolution.get("mirna_ids") or [])
    mirna_ids = {row.get("canonical_id") for row in mirna_rows}
    if len(mirna_rows) != 1 or not mirna_ids.issubset(allowed_mirna):
        return reject("relation_mirna_endpoint_conflict")

    gene_rows = _relation_endpoint_rows(
        rel.get("gene_id"), "gene", entity_store, requested)
    allowed_gene = set(resolution.get("gene_ids") or [])
    gene_ids = {row.get("canonical_id") for row in gene_rows}
    if len(gene_rows) != 1 or not gene_ids.issubset(allowed_gene):
        return reject("relation_gene_endpoint_conflict")

    species_status, _ = _relation_species_status(rel, requested)
    if species_status != "matched":
        return reject("relation_species_tax_conflict")

    entity_arm = parse_mirna_name(mirna_rows[0].get("name") or "").get("arm")
    relation_arm = (rel.get("mirna_arm") or "").strip().lower() or None
    if relation_arm and entity_arm and relation_arm != entity_arm:
        return reject("relation_arm_entity_conflict")

    if rel.get("passage_id") and passage is None:
        return reject("linked_passage_missing")
    if passage and rel.get("doc_id") and passage.get("doc_id") != rel["doc_id"]:
        return reject("linked_passage_document_mismatch")

    curation_status = (rel.get("curation_status") or "unreviewed").strip().lower()
    extraction_method = (rel.get("extraction_method") or "").strip().lower()
    requires_attribution = curation_status != "reviewed" or (
        extraction_method in {"text-mining", "rules-v1"}
    )
    if passage is None:
        if requires_attribution:
            return reject("source_attribution_missing")
        source_status = "curated_without_linked_passage"
    else:
        mirna_terms = _endpoint_text_terms(
            rel.get("mirna_id"), mirna_rows, entity_store)
        gene_terms = _endpoint_text_terms(
            rel.get("gene_id"), gene_rows, entity_store)
        source_status, attribution_state = _source_attribution(
            passage.get("text"), mirna_terms, gene_terms,
            _entity_candidate_record(mirna_rows[0]))
        if attribution_state != "attributed":
            return reject(source_status)

    record = _normalise_relation(
        rel,
        passage,
        snapshot,
        next(iter(mirna_ids)),
        next(iter(gene_ids)),
        source_status,
    )
    return record, None


def _prov(store: EvidenceStore, started: float) -> dict:
    coverage = store.coverage()
    return env.provenance(
        SOURCE,
        snapshot=",".join(coverage["snapshots"]),
        started_at=started,
        search_mode=coverage["search_mode"],
        schema_version=coverage["schema_version"],
    )


def _not_loaded_error(started: float, exc: NotLoaded) -> dict:
    return env.err(
        env.NOT_LOADED,
        str(exc),
        env.provenance(SOURCE, started_at=started),
        detail=dict(
            exc.coverage(),
            hint="python scripts/evidence_ingest.py --help",
        ),
    )


def _limited_note(prefix: str = "") -> str:
    text = (
        "Results cover only the indexed corpus and snapshot; this limited "
        "corpus is not evidence of absence."
    )
    return f"{prefix} {text}".strip()

def literature_search(mirna: str, gene: str, species: str = "hsa",
                      top_k: int = 10) -> dict:
    started = time.monotonic()
    if top_k < 1 or top_k > 100:
        return env.err(
            env.INVALID_PARAMS,
            "top_k must be between 1 and 100",
            env.provenance(SOURCE, started_at=started),
        )
    try:
        store = EvidenceStore.open()
    except NotLoaded as exc:
        return _not_loaded_error(started, exc)
    try:
        resolution = resolve_pair(mirna, gene, species)
        mirna_endpoint = resolution["endpoints"].get("mirna", {})
        gene_endpoint = resolution["endpoints"].get("gene", {})
        mirna_terms = _surface_terms(mirna, mirna_endpoint, mirna=True)
        gene_terms = _surface_terms(gene, gene_endpoint, mirna=False)
        requested = resolution["requested_species"]

        hits = store.search_passages(
            " ".join(_dedupe([mirna, gene, *mirna_terms, *gene_terms])),
            limit=max(top_k * 4, 20),
        )
        candidates: list[dict] = []
        candidate_ids: set[str] = set()
        for passage in hits:
            candidate = _candidate_record(passage, mirna_terms, gene_terms)
            if not candidate["matched_sides"] or candidate["passage_id"] in candidate_ids:
                continue
            candidate_ids.add(candidate["passage_id"])
            candidates.append(candidate)

        evidence: list[dict] = []
        excluded_relations: list[dict] = []
        if resolution["resolved"]:
            entity_store = EntityStore.open()
            try:
                relations = store.relations_for(
                    resolution["mirna_ids"],
                    resolution["gene_ids"],
                    mirna_aliases=_surface_terms(
                        mirna, mirna_endpoint, mirna=True),
                    gene_aliases=_surface_terms(
                        gene, gene_endpoint, mirna=False),
                )
                for rel in relations:
                    passage = (
                        store.get_passage(rel["passage_id"])
                        if rel.get("passage_id") else None
                    )
                    record, exclusion = _assess_relation(
                        rel, passage, requested, resolution, entity_store, store)
                    if exclusion is not None:
                        excluded_relations.append(exclusion)
                    else:
                        evidence.append(record)
                    if passage and passage["passage_id"] not in candidate_ids:
                        candidates.append(_candidate_record(
                            passage, mirna_terms, gene_terms))
                        candidate_ids.add(passage["passage_id"])
            finally:
                entity_store.close()

        status = resolution["status"]
        if status == "ambiguous":
            outcome = "ambiguous"
            note = _limited_note(
                "No candidate passage was promoted to evidence because entity, "
                "species, or arm resolution is ambiguous."
            )
        elif status == "unresolved":
            outcome = "unresolved"
            note = _limited_note(
                "No candidate passage was promoted to evidence because entity "
                "resolution is unresolved."
            )
        elif requested["known"] is False:
            outcome = "species_unconfirmed"
            note = _limited_note(
                "Species applicability is unknown, so species-specific evidence "
                "was not asserted."
            )
        elif any(e["evidence_status"] == "positive_relation_claim"
                 for e in evidence):
            outcome = "structured_relations_found"
            note = _limited_note(
                "Structured relation claims passed canonical identity, metadata, "
                "source, and passage checks; this is not a prediction."
            )
        elif evidence:
            outcome = "nonpositive_relation_claims"
            note = _limited_note(
                "Structured relation claims were found, but none has a positive "
                "polarity; inspect negative, speculative, or unknown metadata."
            )
        elif candidates:
            outcome = "no_structured_relations_found"
            note = _limited_note(
                "Text was retrieved, but no species-applicable structured "
                "relation passed the evidence checks."
            )
        else:
            outcome = "no_retrieved_evidence"
            note = _limited_note(
                "No passage matched either endpoint in this index."
            )

        coverage = store.coverage()
        return env.ok({
            "query": {"mirna": mirna, "gene": gene, "species": species},
            "schema_version": SCHEMA_VERSION,
            "resolution": resolution,
            "outcome": outcome,
            "candidate_passages": candidates[:top_k],
            "evidence": evidence[:top_k],
            "excluded_relations": excluded_relations[:top_k],
            "coverage": coverage,
            "limited_corpus": True,
            "note": note,
        }, _prov(store, started))
    finally:
        store.close()


def evidence_lookup(mirna: str, gene: str, species: str = "hsa") -> dict:
    started = time.monotonic()
    try:
        store = EvidenceStore.open()
    except NotLoaded as exc:
        return _not_loaded_error(started, exc)
    try:
        resolution = resolve_pair(mirna, gene, species)
        coverage = store.coverage()
        if not resolution["resolved"]:
            return env.err(
                env.UNRESOLVED,
                f"pair resolution is {resolution['status']}: {resolution['reason']}",
                _prov(store, started),
                detail={
                    "schema_version": SCHEMA_VERSION,
                    "resolution": resolution,
                    "coverage": coverage,
                    "limited_corpus": True,
                    "note": _limited_note(),
                },
            )
        requested = resolution["requested_species"]
        if not requested["known"]:
            return env.ok({
                "query": {"mirna": mirna, "gene": gene, "species": species},
                "schema_version": SCHEMA_VERSION,
                "resolution": resolution,
                "outcome": "species_unconfirmed",
                "relations": [],
                "excluded_relations": [],
                "coverage": coverage,
                "limited_corpus": True,
                "note": _limited_note(
                    "Species applicability is unknown, so no species-specific "
                    "relation was asserted."
                ),
            }, _prov(store, started))

        records: list[dict] = []
        excluded: list[dict] = []
        entity_store = EntityStore.open()
        try:
            mirna_endpoint = resolution["endpoints"].get("mirna", {})
            gene_endpoint = resolution["endpoints"].get("gene", {})
            relations = store.relations_for(
                resolution["mirna_ids"],
                resolution["gene_ids"],
                mirna_aliases=_surface_terms(
                    mirna, mirna_endpoint, mirna=True),
                gene_aliases=_surface_terms(
                    gene, gene_endpoint, mirna=False),
            )
            for rel in relations:
                passage = (
                    store.get_passage(rel["passage_id"])
                    if rel.get("passage_id") else None
                )
                record, exclusion = _assess_relation(
                    rel, passage, requested, resolution, entity_store, store)
                if exclusion is not None:
                    excluded.append(exclusion)
                else:
                    records.append(record)
        finally:
            entity_store.close()
        outcome = (
            "structured_relations_found" if records
            else "no_structured_relations_found"
        )
        return env.ok({
            "query": {"mirna": mirna, "gene": gene, "species": species},
            "schema_version": SCHEMA_VERSION,
            "resolution": resolution,
            "outcome": outcome,
            "relations": records,
            "excluded_relations": excluded,
            "coverage": coverage,
            "limited_corpus": True,
            "note": _limited_note(
                "No structured relation passed the evidence checks."
                if not records else
                "Structured relations are shown with their original provenance "
                "and curation metadata."
            ),
        }, _prov(store, started))
    finally:
        store.close()

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _overlap(claim: str, passage: str) -> float:
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 0.0
    passage_tokens = set(_tokens(passage))
    return sum(1 for token in claim_tokens if token in passage_tokens) / len(claim_tokens)


def citation_verify(pmid_or_doc_id: str, claim: str,
                    min_overlap: float = 0.8) -> dict:
    """Check literal quotation and report scientific support separately.

    Automatic semantic support remains ``not_assessed``. The legacy
    ``verified`` field therefore remains false; token overlap is only a
    candidate-ranking signal and cannot promote a quote to supported science.
    """
    started = time.monotonic()
    if not 0 <= min_overlap <= 1:
        return env.err(
            env.INVALID_PARAMS,
            "min_overlap must be between 0 and 1",
            env.provenance(SOURCE, started_at=started),
        )
    try:
        store = EvidenceStore.open()
    except NotLoaded as exc:
        return _not_loaded_error(started, exc)
    try:
        doc = store.get_document(pmid_or_doc_id)
        coverage = store.coverage()
        if not doc:
            return env.err(
                env.UNRESOLVED,
                f"document {pmid_or_doc_id!r} is not in the index",
                _prov(store, started),
                detail={
                    "schema_version": SCHEMA_VERSION,
                    "coverage": coverage,
                    "limited_corpus": True,
                },
            )
        passages = store.passages_for(doc["doc_id"])
        best_passage = None
        best_score = -1.0
        exact_span: list[int] | None = None
        for passage in passages:
            text = passage.get("text") or ""
            start = text.find(claim) if claim else -1
            if start >= 0:
                if best_passage is None or exact_span is None:
                    best_passage = passage
                    best_score = 1.0
                    exact_span = [start, start + len(claim)]
                continue
            score = _overlap(claim, text)
            if score > best_score:
                best_passage = passage
                best_score = score
                exact_span = None

        best_match = None
        if best_passage:
            text = best_passage.get("text") or ""
            best_match = {
                "passage_id": best_passage["passage_id"],
                "section": best_passage.get("section"),
                "overlap_score": round(max(best_score, 0.0), 6),
                "meets_min_overlap": max(best_score, 0.0) >= min_overlap,
                "exact_substring": exact_span is not None,
                "quote_span": exact_span,
                "quote_span_basis": "passage_relative_zero_based_half_open",
                "stored_passage_span": _stored_span(best_passage),
                "source_text_hash": _source_hash(text),
                "snippet": _snippet(text, _tokens(claim)),
                "candidate_only": True,
            }

        if exact_span is not None:
            outcome = "exact_quote_found"
            quote_match = {
                "status": "exact",
                "span": exact_span,
                "basis": "passage_relative_zero_based_half_open",
                "passage_id": best_passage["passage_id"],
                "source_text_hash": _source_hash(best_passage.get("text")),
            }
            note = _limited_note(
                "The exact quotation is present. Presence alone does not assess "
                "whether it supports a positive scientific claim."
            )
        elif best_passage:
            outcome = "quote_not_found"
            quote_match = {
                "status": "none",
                "span": None,
                "basis": "passage_relative_zero_based_half_open",
                "passage_id": None,
                "source_text_hash": None,
            }
            note = _limited_note(
                "No exact quotation was found in the indexed passages; the "
                "overlap-ranked passage is a candidate only."
            )
        else:
            outcome = "no_retrieved_evidence"
            quote_match = {
                "status": "none",
                "span": None,
                "basis": "passage_relative_zero_based_half_open",
                "passage_id": None,
                "source_text_hash": None,
            }
            note = _limited_note(
                "This document has no indexed passages available for checking."
            )

        return env.ok({
            "schema_version": SCHEMA_VERSION,
            "pmid": doc.get("pmid"),
            "doc_id": doc["doc_id"],
            "title": doc.get("title"),
            "snapshot": doc.get("snapshot"),
            "claim": claim,
            "outcome": outcome,
            "quote_match": quote_match,
            "claim_support": "not_assessed",
            "claim_support_detail": {
                "basis": "no_reviewed_claim_passage_annotation",
                "automatic_semantic_assessment": False,
            },
            "verified": False,
            "verified_deprecated": (
                "true only for reviewed, supported claim-passage annotations; "
                "exact quotation alone is insufficient"
            ),
            "best_match": best_match,
            "min_overlap": min_overlap,
            "min_overlap_semantics": "candidate_ranking_only",
            "n_passages": len(passages),
            "coverage": coverage,
            "limited_corpus": True,
            "note": note,
        }, _prov(store, started))
    finally:
        store.close()
