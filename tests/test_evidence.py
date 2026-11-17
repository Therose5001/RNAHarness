"""Evidence-layer regression tests for schema v2 and cautious semantics."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from rnaharness import envelope as env
from rnaharness.entity import EntityStore
from rnaharness.entity.loaders import load_hgnc, load_mirbase
from rnaharness.evidence import EvidenceStore
from rnaharness.evidence import search as evidence

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = "2026-11-03"
TARGET_PASSAGE = (
    "Results: hsa-miR-21 directly targets the PTEN 3' UTR in HEK293 cells."
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    entity_db = tmp_path / "entities.sqlite"
    ent = EntityStore.open(entity_db, create=True)
    load_mirbase(
        ent,
        FIXTURES / "mature_mini.fa",
        FIXTURES / "mirbase_aliases.txt",
        SNAPSHOT,
    )
    load_hgnc(ent, FIXTURES / "hgnc_mini.tsv", SNAPSHOT)
    mouse_mirna = ent.upsert_entity(
        "mirna",
        "MIMATMMU0001",
        name="mmu-miR-21-5p",
        species="mmu",
        taxid="10090",
        source="test",
        snapshot=SNAPSHOT,
    )
    ent.add_alias(mouse_mirna, "mmu-miR-21-5p", "test")
    ent.add_alias(mouse_mirna, "miR-21", "test")
    mouse_gene = ent.upsert_entity(
        "gene",
        "MGI:1",
        name="Pten",
        species="mmu",
        taxid="10090",
        source="test",
        snapshot=SNAPSHOT,
    )
    ent.add_alias(mouse_gene, "PTEN", "test")
    ent.upsert_entity(
        "gene",
        "HGNC:1676",
        name="CDKN1A",
        species="hsa",
        taxid="9606",
        source="test",
        snapshot=SNAPSHOT,
    )
    ent.upsert_entity(
        "mirna",
        "MIMATTEST999",
        name="hsa-miR-999-5p",
        species="hsa",
        taxid="9606",
        source="test",
        snapshot=SNAPSHOT,
    )
    ent.set_meta("source:test-entities", "fixture")
    ent.commit()
    ent.close()
    monkeypatch.setenv("RNAHARNESS_ENTITY_DB", str(entity_db))

    db = tmp_path / "evidence.sqlite"
    ev = EvidenceStore.open(db, create=True)
    documents = [
        ("PMC9990001", "99900001", "miR-21 targets PTEN"),
        ("PMC9990002", "99900002", "Negated targeting claim"),
        ("PMC9990003", "99900003", "miR-21-3p relation"),
        ("PMC9990004", "99900004", "miR-21 and TP53"),
        ("PMC9990005", "99900005", "Unknown gene"),
        ("PMC9990006", "99900006", "Boundary controls"),
    ]
    for doc_id, pmid, title in documents:
        ev.upsert_document({
            "doc_id": doc_id,
            "pmid": pmid,
            "title": title,
            "journal": "Test Journal",
            "year": 2026,
            "license": "CC-BY",
            "source": "test",
            "snapshot": SNAPSHOT,
        })

    passages = [
        ("PMC9990001:results:1", "PMC9990001", "results", TARGET_PASSAGE, 0, len(TARGET_PASSAGE)),
        (
            "PMC9990002:results:1",
            "PMC9990002",
            "results",
            "Results: hsa-miR-21-5p never targets PTEN in HEK293 cells.",
            0,
            61,
        ),
        (
            "PMC9990003:results:1",
            "PMC9990003",
            "results",
            "Results: hsa-miR-21-3p does not target PTEN in HEK293 cells.",
            0,
            63,
        ),
        (
            "PMC9990004:results:1",
            "PMC9990004",
            "results",
            "Results: hsa-miR-21-5p and TP53 were discussed together.",
            0,
            59,
        ),
        (
            "PMC9990005:results:1",
            "PMC9990005",
            "results",
            "Results: hsa-miR-21-5p was discussed with NOPE.",
            0,
            50,
        ),
        (
            "PMC9990006:results:1",
            "PMC9990006",
            "results",
            "Results: hsa-miR-21-5p and PTENP1 are distinct.",
            0,
            49,
        ),
        (
            "PMC9990006:results:2",
            "PMC9990006",
            "results",
            "Results: hsa-miR-210-5p and PTEN are distinct.",
            0,
            48,
        ),
    ]
    for passage_id, doc_id, section, text, start, end in passages:
        ev.add_passage(passage_id, doc_id, section, text, start, end)

    mouse_text = "Results: mmu-miR-21-5p targets Pten in mouse cells."
    ev.add_passage("PMC9990001:mouse:1", "PMC9990001", "results",
                   mouse_text, 0, len(mouse_text))

    ev.add_relation({
        "relation_id": "rel-hsa-positive",
        "mirna_id": "MIMAT0000076",
        "gene_id": "HGNC:9588",
        "relation_type": "targeting",
        "method": "CLASH",
        "doc_id": "PMC9990001",
        "passage_id": "PMC9990001:results:1",
        "confidence": "explicit",
        "extracted_by": "manual",
        "species": "hsa",
        "tax_id": "9606",
        "mirna_arm": "5p",
        "polarity": "positive",
        "experimental_method": "CLASH",
        "curation_status": "reviewed",
        "extraction_method": "curation",
    })
    ev.add_relation({
        "relation_id": "rel-mmu-positive",
        "mirna_id": "MIMATMMU0001",
        "gene_id": "MGI:1",
        "relation_type": "targeting",
        "method": "reporter assay",
        "doc_id": "PMC9990001",
        "passage_id": "PMC9990001:mouse:1",
        "confidence": "explicit",
        "extracted_by": "manual",
        "species": "mmu",
        "tax_id": "10090",
        "mirna_arm": "5p",
        "polarity": "positive",
        "experimental_method": "reporter assay",
        "curation_status": "reviewed",
        "extraction_method": "curation",
    })
    ev.add_relation({
        "relation_id": "rel-hsa-negative",
        "mirna_id": "MIMAT0004494",
        "gene_id": "HGNC:9588",
        "relation_type": "targeting",
        "method": "text-mining",
        "doc_id": "PMC9990003",
        "passage_id": "PMC9990003:results:1",
        "confidence": "implied",
        "extracted_by": "rules-v1",
        "species": "hsa",
        "tax_id": "9606",
        "mirna_arm": "3p",
        "polarity": "negative",
        "experimental_method": None,
        "curation_status": "unreviewed",
        "extraction_method": "text-mining",
    })
    ev.set_meta("source:test", "6")
    ev.set_meta("snapshot:index", SNAPSHOT)
    ev.commit()
    ev.close()
    monkeypatch.setenv("RNAHARNESS_EVIDENCE_DB", str(db))
    return db


def test_exact_quotation_is_not_claim_support(store):
    passage = "Results: hsa-miR-21-5p never targets PTEN in HEK293 cells."
    claim = "hsa-miR-21-5p never targets PTEN"
    payload = evidence.citation_verify("99900002", claim)
    assert payload["ok"] is True
    assert payload["data"]["outcome"] == "exact_quote_found"
    assert payload["data"]["quote_match"]["status"] == "exact"
    assert payload["data"]["claim_support"] == "not_assessed"
    assert payload["data"]["verified"] is False
    assert payload["data"]["best_match"]["exact_substring"] is True
    assert payload["data"]["best_match"]["quote_span"] == [9, 9 + len(claim)]
    assert payload["data"]["best_match"]["quote_span_basis"] == (
        "passage_relative_zero_based_half_open"
    )
    assert payload["data"]["best_match"]["source_text_hash"] == hashlib.sha256(
        passage.encode("utf-8")
    ).hexdigest()
    assert payload["data"]["best_match"]["stored_passage_span"]["span"] == [0, 61]
    assert payload["data"]["best_match"]["stored_passage_span"]["basis"] == (
        "stored_normalized_document_offsets"
    )


def test_exact_quotation_offsets_and_hash_for_positive_text(store):
    claim = "hsa-miR-21 directly targets the PTEN 3' UTR"
    payload = evidence.citation_verify("PMC9990001", claim)
    data = payload["data"]
    assert data["quote_match"]["span"] == [9, 9 + len(claim)]
    assert data["best_match"]["source_text_hash"] == hashlib.sha256(
        TARGET_PASSAGE.encode("utf-8")
    ).hexdigest()
    assert data["claim_support_detail"]["automatic_semantic_assessment"] is False


def test_hsa_and_mmu_evidence_are_not_identical(store):
    human = evidence.literature_search("hsa-miR-21-5p", "PTEN", "hsa")
    mouse = evidence.literature_search("mmu-miR-21-5p", "PTEN", "mmu")
    assert human["data"]["outcome"] == "structured_relations_found"
    assert mouse["data"]["outcome"] == "structured_relations_found"
    human_ids = {item["relation_id"] for item in human["data"]["evidence"]}
    mouse_ids = {item["relation_id"] for item in mouse["data"]["evidence"]}
    assert human_ids == {"rel-hsa-positive"}
    assert mouse_ids == {"rel-mmu-positive"}
    assert human["data"]["evidence"][0]["tax_id"] == "9606"
    assert mouse["data"]["evidence"][0]["tax_id"] == "10090"


def test_negative_relation_is_a_nonpositive_claim(store):
    payload = evidence.literature_search("hsa-miR-21-3p", "PTEN", "hsa")
    assert payload["data"]["outcome"] == "nonpositive_relation_claims"
    record = payload["data"]["evidence"][0]
    assert record["polarity"] == "negative"
    assert record["evidence_status"] == "negative_relation_claim"
    assert record["confidence"] == "implied"
    assert record["extraction_method"] == "text-mining"


def test_candidates_remain_separate_from_evidence(store):
    payload = evidence.literature_search("hsa-miR-21-5p", "TP53", "hsa")
    assert payload["data"]["outcome"] == "no_structured_relations_found"
    assert payload["data"]["evidence"] == []
    candidate = payload["data"]["candidate_passages"][0]
    assert candidate["candidate_only"] is True
    assert candidate["pair_mentioned"] is True


def test_unknown_gene_leaves_candidate_only(store):
    payload = evidence.literature_search("hsa-miR-21-5p", "NOPE", "hsa")
    assert payload["data"]["outcome"] == "unresolved"
    assert payload["data"]["evidence"] == []
    candidates = payload["data"]["candidate_passages"]
    assert candidates
    assert candidates[0]["candidate_only"] is True
    assert candidates[0]["pair_mentioned"] is True


def test_entity_boundaries_do_not_match_prefixes(store):
    payload = evidence.literature_search("hsa-miR-21-5p", "PTEN", "hsa")
    candidates = {
        item["passage_id"]: item for item in payload["data"]["candidate_passages"]
    }
    ptenp1 = candidates["PMC9990006:results:1"]
    mir210 = candidates["PMC9990006:results:2"]
    assert ptenp1["matched_sides"] == ["mirna"]
    assert ptenp1["pair_mentioned"] is False
    assert mir210["matched_sides"] == ["gene"]
    assert mir210["pair_mentioned"] is False


def test_explicit_arms_resolve_and_generic_name_is_ambiguous(store):
    five = evidence.resolve_pair("hsa-miR-21-5p", "PTEN", "hsa")
    three = evidence.resolve_pair("hsa-miR-21-3p", "PTEN", "hsa")
    generic = evidence.resolve_pair("miR-21", "PTEN", "hsa")
    assert five["status"] == "resolved"
    assert five["endpoints"]["mirna"]["arm"] == "5p"
    assert three["status"] == "resolved"
    assert three["endpoints"]["mirna"]["arm"] == "3p"
    assert generic["status"] == "ambiguous"
    assert generic["endpoints"]["mirna"]["reason"] == "multiple_canonical_entities"


def test_species_conflict_and_unknown_species(store):
    conflict = evidence.resolve_pair("hsa-miR-21-5p", "PTEN", "mmu")
    assert conflict["status"] == "ambiguous"
    assert conflict["endpoints"]["mirna"]["reason"] == "explicit_species_conflict"
    unknown = evidence.literature_search("hsa-miR-21-5p", "TP53", "xenopus")
    assert unknown["ok"] is True
    assert unknown["data"]["outcome"] == "species_unconfirmed"
    assert unknown["data"]["evidence"] == []


def test_no_supported_relation_and_no_retrieved_hit(store):
    no_supported = evidence.literature_search("hsa-miR-21-5p", "TP53", "hsa")
    no_hit = evidence.literature_search("hsa-miR-999-5p", "CDKN1A", "hsa")
    assert no_supported["data"]["outcome"] == "no_structured_relations_found"
    assert no_hit["data"]["outcome"] == "no_retrieved_evidence"
    assert no_hit["data"]["candidate_passages"] == []
    assert "not evidence of absence" in no_hit["data"]["note"]


def test_missing_evidence_db_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("RNAHARNESS_EVIDENCE_DB", str(tmp_path / "absent.sqlite"))
    payload = evidence.literature_search("hsa-miR-21-5p", "PTEN")
    assert payload["ok"] is False
    assert payload["error"]["code"] == env.NOT_LOADED
    assert "evidence_ingest.py" in payload["error"]["detail"]["hint"]


def test_empty_existing_evidence_db_is_not_loaded(tmp_path, monkeypatch):
    path = tmp_path / "empty.sqlite"
    path.touch()
    before = _hash(path)
    monkeypatch.setenv("RNAHARNESS_EVIDENCE_DB", str(path))
    payload = evidence.citation_verify("99900001", "claim")
    assert payload["error"]["code"] == env.NOT_LOADED
    assert _hash(path) == before


def test_legacy_schema_opens_read_only_without_mutation(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE documents (doc_id TEXT PRIMARY KEY, pmid TEXT);
        CREATE TABLE passages (
          passage_id TEXT PRIMARY KEY, doc_id TEXT, section TEXT, text TEXT,
          char_start INTEGER, char_end INTEGER
        );
        CREATE TABLE relations (
          relation_id TEXT PRIMARY KEY, mirna_id TEXT, gene_id TEXT,
          relation_type TEXT, method TEXT, doc_id TEXT, passage_id TEXT,
          confidence TEXT, extracted_by TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()
    before = _hash(path)
    opened = EvidenceStore.open(path)
    assert opened.read_only is True
    assert opened.coverage()["schema_version"] == 1
    assert opened.conn.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        opened.set_meta("schema_version", "2")
    opened.close()
    assert _hash(path) == before


def test_positional_call_shapes_and_mcp_serialization(store):
    from rnaharness.servers import evidence_mcp

    literature = evidence_mcp.tool_literature_search(
        "hsa-miR-21-5p", "PTEN", "hsa", 2
    )
    lookup = evidence_mcp.tool_evidence_lookup("hsa-miR-21-5p", "PTEN", "hsa")
    citation = evidence_mcp.tool_citation_verify(
        "99900001", "hsa-miR-21 directly targets the PTEN 3' UTR", 0.8
    )
    assert literature["data"]["evidence"][0]["relation_id"] == "rel-hsa-positive"
    assert lookup["data"]["relations"][0]["relation_id"] == "rel-hsa-positive"
    assert citation["data"]["quote_match"]["status"] == "exact"
    for payload in (literature, lookup, citation):
        encoded = json.dumps(payload, ensure_ascii=False)
        assert json.loads(encoded)["ok"] is True


def test_mcp_server_exposes_three_tools(store):
    import asyncio
    from rnaharness.servers import evidence_mcp

    tools = asyncio.run(evidence_mcp.server.list_tools())
    assert {tool.name for tool in tools} == {
        "literature_search", "evidence_lookup", "citation_verify"
    }

def _open_writer(db):
    return EvidenceStore.open(db, create=True)


def test_extracted_surface_ids_are_retrievable_and_strictly_resolved(store):
    from scripts import pubmed_ingest

    text = "hsa-miR-21-5p directly targets PTEN in HEK293 cells."
    passage_id = "PMC9990007:results:1"
    ev = _open_writer(store)
    ev.upsert_document({
        "doc_id": "PMC9990007",
        "pmid": "99900007",
        "title": "Surface identifier relation",
        "journal": "Test Journal",
        "year": 2026,
        "license": "CC-BY",
        "source": "test",
        "snapshot": SNAPSHOT,
    })
    ev.add_passage(
        passage_id, "PMC9990007", "results", text, 0, len(text)
    )
    relation = pubmed_ingest.extract_relations(
        [{"doc_id": "PMC9990007", "passage_id": passage_id, "text": text}],
        [("hsa-miR-21-5p", "PTEN", "hsa")],
    )[0]
    assert relation["mirna_id"] == "hsa-miR-21-5p"
    assert relation["gene_id"] == "PTEN"
    ev.add_relation(relation)
    ev.commit()
    ev.close()

    for mirna, gene in [
        ("hsa-miR-21-5p", "PTEN"),
        ("MIMAT0000076", "HGNC:9588"),
    ]:
        literature = evidence.literature_search(mirna, gene, "hsa")
        lookup = evidence.evidence_lookup(mirna, gene, "hsa")
        literature_records = {
            item["relation_id"]: item for item in literature["data"]["evidence"]
        }
        lookup_records = {
            item["relation_id"]: item for item in lookup["data"]["relations"]
        }
        assert relation["relation_id"] in literature_records
        assert relation["relation_id"] in lookup_records
        for record in (
            literature_records[relation["relation_id"]],
            lookup_records[relation["relation_id"]],
        ):
            assert record["snapshot"] == SNAPSHOT
            assert record["evidence_status"] == "positive_relation_claim"
            assert record["source_attribution_status"] == (
                "same_clause_endpoints_and_relation_cue"
            )


def test_relation_metadata_document_and_endpoint_exclusions(store):
    ev = _open_writer(store)
    only_mirna = "Only hsa-miR-21-5p is mentioned."
    ev.add_passage(
        "PMC9990001:results:only-mirna",
        "PMC9990001",
        "results",
        only_mirna,
        0,
        len(only_mirna),
    )
    base = {
        "relation_type": "targeting",
        "method": "text-mining",
        "confidence": "implied",
        "extracted_by": "rules-v1",
        "species": "hsa",
        "tax_id": "9606",
        "mirna_arm": "5p",
        "polarity": "positive",
        "experimental_method": None,
        "curation_status": "unreviewed",
        "extraction_method": "text-mining",
    }
    relations = [
        {
            **base,
            "relation_id": "rel-arm-conflict",
            "mirna_id": "MIMAT0000076",
            "gene_id": "HGNC:9588",
            "doc_id": "PMC9990001",
            "passage_id": "PMC9990001:results:1",
            "mirna_arm": "3p",
        },
        {
            **base,
            "relation_id": "rel-species-tax-conflict",
            "mirna_id": "hsa-miR-21-5p",
            "gene_id": "PTEN",
            "doc_id": "PMC9990001",
            "passage_id": "PMC9990001:results:1",
            "tax_id": "10090",
        },
        {
            **base,
            "relation_id": "rel-wrong-document",
            "mirna_id": "hsa-miR-21-5p",
            "gene_id": "PTEN",
            "doc_id": "PMC9990002",
            "passage_id": "PMC9990001:results:1",
        },
        {
            **base,
            "relation_id": "rel-missing-passage",
            "mirna_id": "hsa-miR-21-5p",
            "gene_id": "PTEN",
            "doc_id": "PMC9990001",
            "passage_id": "PMC9990001:results:missing",
        },
        {
            **base,
            "relation_id": "rel-single-endpoint",
            "mirna_id": "hsa-miR-21-5p",
            "gene_id": "PTEN",
            "doc_id": "PMC9990001",
            "passage_id": "PMC9990001:results:only-mirna",
        },
    ]
    for relation in relations:
        ev.add_relation(relation)
    ev.commit()
    ev.close()

    payload = evidence.evidence_lookup(
        "hsa-miR-21-5p", "PTEN", "hsa"
    )["data"]
    excluded = {
        item["relation_id"]: item
        for item in payload["excluded_relations"]
    }
    assert excluded["rel-arm-conflict"]["reason"] == (
        "relation_arm_entity_conflict"
    )
    assert excluded["rel-species-tax-conflict"]["reason"] == (
        "relation_species_tax_conflict"
    )
    assert excluded["rel-wrong-document"]["reason"] == (
        "linked_passage_document_mismatch"
    )
    assert excluded["rel-missing-passage"]["reason"] == (
        "linked_passage_missing"
    )
    assert excluded["rel-single-endpoint"]["reason"] == (
        "linked_passage_missing_gene_endpoint"
    )
    assert all("snapshot" in item for item in excluded.values())
    assert {
        item["relation_id"] for item in payload["relations"]
    } == {"rel-hsa-positive"}


def test_cross_clause_stored_relation_is_not_promoted(store):
    text = "hsa-miR-21-5p targets BCL2, while PTEN was also measured."
    ev = _open_writer(store)
    ev.upsert_document({
        "doc_id": "PMC9990008",
        "pmid": "99900008",
        "title": "Cross-clause controls",
        "journal": "Test Journal",
        "year": 2026,
        "license": "CC-BY",
        "source": "test",
        "snapshot": SNAPSHOT,
    })
    ev.add_passage(
        "PMC9990008:results:1", "PMC9990008", "results", text, 0, len(text)
    )
    ev.add_relation({
        "relation_id": "rel-cross-clause",
        "mirna_id": "hsa-miR-21-5p",
        "gene_id": "PTEN",
        "relation_type": "targeting",
        "method": "text-mining",
        "doc_id": "PMC9990008",
        "passage_id": "PMC9990008:results:1",
        "confidence": "implied",
        "extracted_by": "rules-v1",
        "species": "hsa",
        "tax_id": "9606",
        "mirna_arm": "5p",
        "polarity": "positive",
        "experimental_method": None,
        "curation_status": "unreviewed",
        "extraction_method": "text-mining",
    })
    ev.commit()
    ev.close()

    payload = evidence.evidence_lookup(
        "hsa-miR-21-5p", "PTEN", "hsa"
    )["data"]
    assert "rel-cross-clause" not in {
        item["relation_id"] for item in payload["relations"]
    }
    excluded = {
        item["relation_id"]: item
        for item in payload["excluded_relations"]
    }
    assert excluded["rel-cross-clause"]["reason"] == (
        "source_attribution_missing"
    )
    assert excluded["rel-cross-clause"]["snapshot"] == SNAPSHOT


@pytest.mark.parametrize("source_text,expected_count", [
    ("hsa-miR-21-5p targets PTEN in HEK293 cells.", 1),
    ("hsa-miR-21-5p was measured. PTEN targets another transcript.", 0),
    ("hsa-miR-21-3p targets PTEN in HEK293 cells.", 0),
    ("mmu-miR-21-5p targets PTEN in mouse cells.", 0),
    ("miR-21 targets PTEN in mouse cells.", 0),
    ("miR-21 targets PTEN in human and mouse cells.", 0),
    ("hsa-miR-21-3p targets BCL2. PTEN and hsa-miR-21-5p were measured.", 0),
])
def test_linked_source_respects_sentence_species_and_arm(store, source_text, expected_count):
    ev = _open_writer(store)
    ev.conn.execute("DELETE FROM relations")
    ev.add_passage("source-control", "PMC9990001", "results", source_text, 0, len(source_text))
    ev.add_relation({
        "relation_id": "source-control", "mirna_id": "MIMAT0000076",
        "gene_id": "HGNC:9588", "relation_type": "targeting",
        "doc_id": "PMC9990001", "passage_id": "source-control",
        "species": "hsa", "tax_id": "9606", "mirna_arm": "5p",
        "confidence": "implied", "extraction_method": "text-mining",
        "curation_status": "unreviewed", "polarity": "positive",
    })
    ev.commit()
    ev.close()
    search = evidence.literature_search("hsa-miR-21-5p", "PTEN")["data"]
    lookup = evidence.evidence_lookup("hsa-miR-21-5p", "PTEN")["data"]
    assert len(search["evidence"]) == expected_count
    assert len(lookup["relations"]) == expected_count
    if not expected_count:
        assert search["excluded_relations"][0]["relation_id"] == "source-control"


def test_v1_store_rejects_v2_relation_before_mutation(tmp_path):
    from rnaharness.evidence.store import EvidenceSchemaError

    path = tmp_path / "legacy-write.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE documents (doc_id TEXT PRIMARY KEY, pmid TEXT);
        CREATE TABLE passages (
          passage_id TEXT PRIMARY KEY, doc_id TEXT, section TEXT, text TEXT,
          char_start INTEGER, char_end INTEGER
        );
        CREATE TABLE relations (
          relation_id TEXT PRIMARY KEY, mirna_id TEXT, gene_id TEXT,
          relation_type TEXT, method TEXT, doc_id TEXT, passage_id TEXT,
          confidence TEXT, extracted_by TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()
    before = _hash(path)

    opened = EvidenceStore.open(path, create=True)
    with pytest.raises(EvidenceSchemaError, match="schema v1"):
        opened.add_relation({
            "relation_id": "rel-v2",
            "mirna_id": "MIMAT0000076",
            "gene_id": "HGNC:9588",
            "relation_type": "targeting",
            "species": "hsa",
            "tax_id": "9606",
            "mirna_arm": "5p",
            "polarity": "positive",
            "curation_status": "reviewed",
            "extraction_method": "curation",
        })
        opened.commit()
    opened.close()
    assert _hash(path) == before



def test_evidence_ingest_preflights_v2_batch_before_document_write(
    tmp_path, monkeypatch
):
    import sys

    from rnaharness.evidence import EvidenceSchemaError
    from scripts import evidence_ingest

    path = tmp_path / "legacy-cli.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE documents (
          doc_id TEXT PRIMARY KEY, pmid TEXT, pmcid TEXT, doi TEXT, title TEXT,
          journal TEXT, year INTEGER, license TEXT, snapshot TEXT, source TEXT
        );
        CREATE TABLE passages (
          passage_id TEXT PRIMARY KEY, doc_id TEXT, section TEXT, text TEXT,
          char_start INTEGER, char_end INTEGER
        );
        CREATE TABLE relations (
          relation_id TEXT PRIMARY KEY, mirna_id TEXT, gene_id TEXT,
          relation_type TEXT, method TEXT, doc_id TEXT, passage_id TEXT,
          confidence TEXT, extracted_by TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()
    documents = tmp_path / "documents.jsonl"
    documents.write_text(json.dumps({
        "doc_id": "PMC-LEDGER",
        "pmid": "99999999",
        "passages": [{
            "passage_id": "PMC-LEDGER:results:1",
            "section": "results",
            "text": "hsa-miR-21-5p targets PTEN.",
        }],
    }) + "\n", encoding="utf-8")
    relations = tmp_path / "relations.jsonl"
    relations.write_text(json.dumps({
        "relation_id": "rel-v2-cli",
        "mirna_id": "MIMAT0000076",
        "gene_id": "HGNC:9588",
        "species": "hsa",
        "tax_id": "9606",
        "mirna_arm": "5p",
        "polarity": "positive",
    }) + "\n", encoding="utf-8")
    before = _hash(path)
    monkeypatch.setattr(sys, "argv", [
        "evidence_ingest.py",
        "--db", str(path),
        "--documents", str(documents),
        "--relations", str(relations),
    ])
    with pytest.raises(EvidenceSchemaError, match="schema v1"):
        evidence_ingest.main()
    assert _hash(path) == before
