"""Entity layer tests: loaders, resolution, and the local_mcp tools.

All fixtures are tiny official-format files, so the suite runs offline.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from rnaharness import envelope as env
from rnaharness.entity import (
    EntityStore,
    NotLoaded,
    mirna_candidates,
    normalize_sequence,
)
from rnaharness.entity.loaders import (
    load_hgnc,
    load_mirbase,
    load_rnacentral_jsonl,
    load_taxonomy,
)
from rnaharness.entity.naming import (
    gene_candidates,
    parse_mirna_name,
    sequence_hash,
)
from rnaharness.servers import local_mcp

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "entities.sqlite"
    store = EntityStore.open(path, create=True)
    load_rnacentral_jsonl(store, FIXTURES / "rnacentral_mini.jsonl", "2026-11-03")
    load_mirbase(store, FIXTURES / "mature_mini.fa",
                 FIXTURES / "mirbase_aliases.txt", "2026-11-03")
    load_hgnc(store, FIXTURES / "hgnc_mini.tsv", "2026-11-03")
    load_taxonomy(store, FIXTURES / "taxonomy" / "names.dmp",
                  FIXTURES / "taxonomy" / "nodes.dmp", "2026-11-03")
    store.close()
    monkeypatch.setenv("RNAHARNESS_ENTITY_DB", str(path))
    return path


# -- naming ----------------------------------------------------------------
def test_sequence_normalisation():
    assert normalize_sequence("acgu acgu") == "ACGUACGU"
    assert normalize_sequence("ACGT") == "ACGU"
    with pytest.raises(ValueError):
        normalize_sequence("ACGU!X")


def test_mirna_candidates_cover_common_styles():
    cands = mirna_candidates("hsa-miR-21-5p")
    assert "hsa-mir-21-5p" in cands
    assert "mir-21" not in cands
    assert "mir-21-5p" not in cands
    assert mirna_candidates("MIR21")[0] == "mir21"
    assert "mir-21" in mirna_candidates("MIR21")
    assert gene_candidates("MMAC1") == ["mmac1"]


def test_mirna_parser_preserves_explicit_species_and_arm():
    parsed = parse_mirna_name("mmu-miR-21-3p")
    assert parsed["species"] == "mmu"
    assert parsed["family"] == "21"
    assert parsed["arm"] == "3p"
    assert parsed["explicit_species"] is True
    assert parsed["explicit_arm"] is True
    generic = parse_mirna_name("miR-21")
    assert generic["species"] is None
    assert generic["explicit_species"] is False
    assert generic["family"] == "21"


# -- loaders ---------------------------------------------------------------
def test_loaders_populate_all_kinds(db):
    store = EntityStore.open(db)
    info = store.info()
    assert info["counts"]["rna"] == 3
    assert info["counts"]["mirna"] == 2
    assert info["counts"]["gene"] == 2
    assert info["counts"]["taxon"] == 4
    assert info["meta"]["source:rnacentral"] == "3 sequences"
    assert info["meta"]["snapshot:hgnc"] == "2026-11-03"
    store.close()


def test_duplicate_sequences_share_a_hash(db):
    store = EntityStore.open(db)
    rows = store.by_sequence(sequence_hash("ACGUACGU"))
    assert {r["canonical_id"] for r in rows} == {"URS0000000001", "URS0000000002"}
    store.close()


# -- tools -----------------------------------------------------------------
def test_rna_identify_exact_and_t_to_u(db):
    payload = local_mcp.rna_identify("ACGTACGT")
    assert payload["ok"] is True
    ids = {m["id"] for m in payload["data"]["matches"]}
    assert ids == {"URS0000000001", "URS0000000002"}
    assert payload["data"]["matches"][0]["sequence"] == "ACGUACGU"


def test_rna_identify_miss_reports_coverage_not_absence(db):
    payload = local_mcp.rna_identify("GGGGCCCC")
    assert payload["ok"] is True
    assert payload["data"]["matches"] == []
    assert "rnacentral" in payload["data"]["coverage"]["sources"]


def test_rna_identify_rejects_non_rna(db):
    payload = local_mcp.rna_identify("ACGUZZ")
    assert payload["ok"] is False
    assert payload["error"]["code"] == env.INVALID_PARAMS


def test_rna_fetch(db):
    payload = local_mcp.rna_fetch("URS0000000003")
    assert payload["ok"] is True
    assert payload["data"]["entities"][0]["sequence"] == "UUUU"


def test_rna_fetch_unknown_is_unresolved(db):
    payload = local_mcp.rna_fetch("URS9999999999")
    assert payload["error"]["code"] == env.UNRESOLVED


def test_mirna_resolve_by_common_name(db):
    payload = local_mcp.mirna_resolve("miR-21")
    assert payload["ok"] is True
    by_id = {m["id"]: m for m in payload["data"]["matches"]}
    five_p = by_id["MIMAT0000076"]
    assert five_p["name"] == "hsa-miR-21-5p"
    assert five_p["seed_7mer"] == "AGCUUAU"
    assert five_p["seed_6mer"] == "AGCUUA"
    assert five_p["arm"] == "5p"
    assert "miRBase" in five_p["xrefs"]


def test_mirna_resolve_by_hgnc_style_name(db):
    payload = local_mcp.mirna_resolve("MIR21")
    ids = {m["id"] for m in payload["data"]["matches"]}
    assert "MIMAT0000076" in ids


def test_gene_resolve_alias_and_previous_name(db):
    for name in ("PTEN", "MMAC1", "TEP1"):
        payload = local_mcp.gene_resolve(name)
        assert payload["ok"] is True, name
        assert payload["data"]["matches"][0]["id"] == "HGNC:9588"
        assert payload["data"]["matches"][0]["xrefs"]["Ensembl"] == ["ENSG00000171862"]


def test_id_map_ensembl_to_entrez(db):
    payload = local_mcp.id_map(["ENSG00000171862"], "NCBI Gene")
    assert payload["ok"] is True
    assert payload["data"]["mapping"][0]["mapped"] == ["5728"]


def test_id_map_reports_unresolved(db):
    payload = local_mcp.id_map(["ENSG00000171862", "NOPE"], "NCBI Gene")
    assert payload["data"]["unresolved"] == ["NOPE"]


def test_taxonomy_resolve_lineage(db):
    payload = local_mcp.taxonomy_resolve("human")
    assert payload["ok"] is True
    match = payload["data"]["matches"][0]
    assert match["taxid"] == "9606"
    assert [step["name"] for step in match["lineage"]] == ["Homo sapiens", "Homo", "root"]


def test_missing_store_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("RNAHARNESS_ENTITY_DB", str(tmp_path / "absent.sqlite"))
    payload = local_mcp.rna_identify("ACGUACGU")
    assert payload["ok"] is False
    assert payload["error"]["code"] == env.NOT_LOADED
    assert "entity_build.py" in payload["error"]["detail"]["hint"]


def test_empty_entity_file_is_not_created_implicitly(tmp_path):
    path = tmp_path / "empty.sqlite"
    path.touch()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(NotLoaded):
        EntityStore.open(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == before


def test_legacy_entity_schema_opens_read_only(tmp_path):
    path = tmp_path / "legacy-entities.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE entities (
          id INTEGER PRIMARY KEY, kind TEXT, canonical_id TEXT, name TEXT,
          species TEXT, taxid TEXT, parent TEXT, sequence TEXT, seq_hash TEXT,
          source TEXT, snapshot TEXT
        );
        CREATE TABLE aliases (
          entity_id INTEGER, alias TEXT, norm TEXT, source TEXT
        );
        CREATE TABLE xrefs (
          entity_id INTEGER, db TEXT, accession TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    store = EntityStore.open(path)
    assert store.read_only is True
    assert store.conn.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        store.set_meta("schema_version", "1")
    store.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# -- rfam_scan -------------------------------------------------------------
def test_parse_tblout():
    text = (
        "5S_rRNA              RF00001   query                -          cm"
        "        1      120        3      118      +    no    1 0.66"
        "   0.0   92.8   1.8e-21 !   5S ribosomal RNA\n"
        "tRNA                 RF00005   query                -          cm"
        "        1       70        4       70      +    no    1 0.5"
        "   0.0   20.0   1.0e-5 !   tRNA\n"
    )
    hits = local_mcp.parse_tblout(text)
    assert [h["family"] for h in hits] == ["5S_rRNA", "tRNA"]
    assert hits[0]["description"] == "5S ribosomal RNA"
    assert hits[0]["evalue"] == 1.8e-21
    assert (hits[0]["seq_from"], hits[0]["seq_to"]) == (3, 118)


def test_rfam_scan_end_to_end(db, tmp_path, monkeypatch):
    cm = tmp_path / "Rfam.cm"
    cm.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("RFAM_CM", str(cm))
    monkeypatch.setenv("RFAM_SCAN_CMD", json.dumps(
        [sys.executable, str(FIXTURES / "fake_cmscan.py")]))
    payload = local_mcp.rfam_scan("ACGUACGU", use_ga=False, evalue=1e-3)
    assert payload["ok"] is True
    assert payload["data"]["n_hits"] == 1
    assert payload["data"]["hits"][0]["family"] == "5S_rRNA"


def test_rfam_scan_missing_dependency(db, tmp_path, monkeypatch):
    monkeypatch.setenv("RFAM_CM", str(tmp_path / "absent.cm"))
    payload = local_mcp.rfam_scan("ACGUACGU")
    assert payload["error"]["code"] == env.DEPENDENCY_MISSING
