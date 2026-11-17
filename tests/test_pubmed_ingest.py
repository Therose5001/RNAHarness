"""Focused tests for explainable PubMed relation extraction."""
from __future__ import annotations

from scripts import pubmed_ingest


def _passage(text: str) -> dict:
    return {"doc_id": "doc1", "passage_id": "doc1:results:1", "text": text}


def test_load_pairs_preserves_old_and_species_aware_shapes(tmp_path):
    path = tmp_path / "pairs.tsv"
    path.write_text(
        "miRNA\tgene\tspecies\n"
        "miR-21\tPTEN\n"
        "mmu-miR-21-5p\tPten\tmmu\n",
        encoding="utf-8",
    )
    assert pubmed_ingest.load_pairs(path) == [
        ("miR-21", "PTEN"),
        ("mmu-miR-21-5p", "Pten", "mmu"),
    ]


def test_extract_relation_preserves_provenance_and_arm_metadata():
    text = "hsa-miR-21 directly targets the PTEN 3' UTR in HEK293 cells."
    relations = pubmed_ingest.extract_relations(
        [_passage(text)], [("hsa-miR-21", "PTEN", "hsa")]
    )
    assert len(relations) == 1
    relation = relations[0]
    assert relation["confidence"] == "implied"
    assert relation["extraction_method"] == "text-mining"
    assert relation["extracted_by"] == "rules-v1"
    assert relation["curation_status"] == "unreviewed"
    assert relation["experimental_method"] is None
    assert relation["species"] == "hsa"
    assert relation["tax_id"] == "9606"
    assert relation["mirna_arm"] is None
    assert relation["polarity"] == "positive"


def test_negation_and_speculation_have_nonpositive_polarity():
    negative = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-5p never targets PTEN in HEK293 cells.")],
        [("hsa-miR-21-5p", "PTEN")],
    )
    speculative = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-5p may target PTEN in HEK293 cells.")],
        [("hsa-miR-21-5p", "PTEN")],
    )
    assert negative[0]["polarity"] == "negative"
    assert speculative[0]["polarity"] == "speculative"


def test_boundaries_prevent_prefix_matches():
    mir210 = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-210-5p targets PTEN in HEK293 cells.")],
        [("hsa-miR-21-5p", "PTEN")],
    )
    ptenp1 = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-5p targets PTENP1 in HEK293 cells.")],
        [("hsa-miR-21-5p", "PTEN")],
    )
    assert mir210 == []
    assert ptenp1 == []


def test_cue_and_entities_must_share_a_sentence():
    relations = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-5p is discussed. PTEN targets another transcript.")],
        [("hsa-miR-21-5p", "PTEN")],
    )
    assert relations == []


def test_species_can_be_supplied_inferred_or_rejected():
    supplied = pubmed_ingest.extract_relations(
        [_passage("mmu-miR-21-5p targets Pten in NIH3T3 cells.")],
        [("mmu-miR-21-5p", "Pten", "mouse")],
    )
    inferred = pubmed_ingest.extract_relations(
        [_passage("miR-21 targets PTEN in HEK293 cells.")],
        [("miR-21", "PTEN")],
    )
    conflict = pubmed_ingest.extract_relations(
        [_passage("mmu-miR-21-5p targets PTEN in human HEK293 cells.")],
        [("mmu-miR-21-5p", "PTEN", "mmu")],
    )
    mixed = pubmed_ingest.extract_relations(
        [_passage("miR-21 targets PTEN in human and mouse cells.")],
        [("miR-21", "PTEN")],
    )
    unknown = pubmed_ingest.extract_relations(
        [_passage("miR-21 targets PTEN in cultured cells.")],
        [("miR-21", "PTEN")],
    )
    assert supplied[0]["species"] == "mmu"
    assert inferred[0]["species"] == "hsa"
    assert conflict == []
    assert mixed == []
    assert unknown == []


def test_explicit_arm_is_preserved():
    relations = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-3p represses PTEN in HEK293 cells.")],
        [("hsa-miR-21-3p", "PTEN", "hsa")],
    )
    assert relations[0]["mirna_arm"] == "3p"


def test_relation_ids_are_deterministic_and_sentence_specific():
    passage = _passage(
        "hsa-miR-21-5p targets PTEN in HEK293 cells. "
        "hsa-miR-21-5p represses PTEN in HEK293 cells."
    )
    first = pubmed_ingest.extract_relations(
        [passage], [("hsa-miR-21-5p", "PTEN", "hsa")]
    )
    second = pubmed_ingest.extract_relations(
        [passage], [("hsa-miR-21-5p", "PTEN", "hsa")]
    )
    assert len(first) == 2
    assert [item["relation_id"] for item in first] == [
        item["relation_id"] for item in second
    ]
    assert len({item["relation_id"] for item in first}) == 2

def test_invalid_explicit_species_is_rejected_without_inference():
    relations = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-5p targets PTEN in HEK293 cells.")],
        [("hsa-miR-21-5p", "PTEN", "nonsense")],
    )
    assert relations == []


def test_cross_clause_mentions_do_not_establish_a_pair_relation():
    relations = pubmed_ingest.extract_relations(
        [_passage("hsa-miR-21-5p targets BCL2, while PTEN was also measured.")],
        [("hsa-miR-21-5p", "PTEN", "hsa")],
    )
    assert relations == []
