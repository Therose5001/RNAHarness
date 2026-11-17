"""MiRBench evaluation harness tests. Offline, tiny generated fixtures."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from scripts.mirbench_eval import (
    SPLITS,
    average_precision,
    auroc,
    ece,
    evaluate_split,
    iter_rows,
    main,
    reverse_complement_rna,
    seed_match_score,
)

HEADER = [
    "gene", "noncodingRNA", "noncodingRNA_name", "noncodingRNA_fam", "feature",
    "label", "chr", "start", "end", "strand", "Nunique", "dominant_region",
    "regions_present", "read_start_in_sel_tx_1based", "read_end_in_sel_tx_1based",
    "gene_cluster_ID", "gene_phyloP", "gene_phastCons",
]
MIR21 = "UAGCUUAUCAGACUGAUGUUGA"
SEED_TARGET = "AUAAGCU" + "A" * 43      # rc contains AGCUUAU (miR-21 7-mer seed)
NO_SEED_TARGET = "C" * 50


def row(gene: str, label: int, nunique: str, region: str, name: str = "hsa-miR-21-5p"):
    values = {
        "gene": gene, "noncodingRNA": MIR21, "noncodingRNA_name": name,
        "noncodingRNA_fam": "mir-21", "feature": "exon", "label": str(label),
        "chr": "chr1", "start": "1", "end": str(len(gene)), "strand": "+",
        "Nunique": nunique, "dominant_region": region,
        "regions_present": region, "read_start_in_sel_tx_1based": "1",
        "read_end_in_sel_tx_1based": "50", "gene_cluster_ID": "g1",
        "gene_phyloP": "[]", "gene_phastCons": "[]",
    }
    return [values[h] for h in HEADER]


def write_fixture(path: Path, rows: list[list[str]], header: list[str] | None = None):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header or HEADER) + "\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")


ROWS = [
    row(SEED_TARGET, 1, "5", "UTR3"),
    row(NO_SEED_TARGET, 0, "0", "UTR3"),
    row(SEED_TARGET, 1, "3", "CDS"),
    row(NO_SEED_TARGET, 0, "0", "CDS"),
    row(SEED_TARGET, 1, "2", "UTR3"),
    row(NO_SEED_TARGET, 0, "0", "UTR3"),
    row(SEED_TARGET, 1, "1", "CDS"),
    row(NO_SEED_TARGET, 0, "0", "CDS"),
]
SCORES = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4]


@pytest.fixture()
def fixture(tmp_path):
    path = tmp_path / "split.tsv.gz"
    write_fixture(path, ROWS)
    scores = tmp_path / "scores.tsv"
    scores.write_text("\n".join(f"{y}\t{s}" for y, s in zip([1, 0] * 4, SCORES)) + "\n",
                      encoding="utf-8")
    return path, scores


# -- metrics ---------------------------------------------------------------
def test_auroc_perfect_and_ties():
    assert auroc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert auroc([0, 1], [0.5, 0.5]) == 0.5
    assert auroc([1, 1], [0.2, 0.8]) is None


def test_average_precision_small_example():
    # pos@0.9 (P=1, R=0.5), neg@0.8 (no recall gain), pos@0.7 (P=2/3, R=1)
    ap = average_precision([1, 0, 1], [0.9, 0.8, 0.7])
    assert abs(ap - (0.5 + 0.5 * 2 / 3)) < 1e-9


def test_ece_known_calibration():
    result = ece([1, 1, 0, 0], [0.9, 0.9, 0.1, 0.1])
    assert abs(result["value"] - 0.1) < 1e-9
    assert ece([1, 0], [2.0, -1.0]) is None


# -- seed match ------------------------------------------------------------
def test_reverse_complement_rna():
    assert reverse_complement_rna("ACGU") == "ACGU"
    assert reverse_complement_rna("AAAA") == "UUUU"


def test_seed_match_planted_hit():
    assert seed_match_score(MIR21, SEED_TARGET) >= 2.0
    assert seed_match_score(MIR21, NO_SEED_TARGET) == 0.0


# -- loader / evaluation ---------------------------------------------------
def test_iter_rows_rejects_missing_label(tmp_path):
    path = tmp_path / "nolabel.tsv.gz"
    header = [h for h in HEADER if h != "label"]
    write_fixture(path, [[v for v, h in zip(ROWS[0], HEADER) if h != "label"]],
                  header=header)
    with pytest.raises(ValueError, match="no `label`"):
        list(iter_rows(path))


def test_evaluate_split_with_scores(fixture, tmp_path):
    path, scores = fixture
    report = evaluate_split(str(path), tmp_path.parent, scores_path=scores)
    assert report["rows_used"] == 8
    assert report["n_pos"] == 4 and report["n_neg"] == 4
    assert report["metrics"]["auroc"] == 1.0
    assert report["metrics"]["ece"] is not None
    assert report["label_matches_nunique"] is True


def test_region_filter(fixture, tmp_path):
    path, scores = fixture
    report = evaluate_split(str(path), tmp_path.parent, regions={"UTR3"},
                            scores_path=scores)
    assert report["rows_used"] == 4
    assert report["n_pos"] == 2


def test_scores_row_count_mismatch(fixture, tmp_path):
    path, _ = fixture
    bad = tmp_path / "short.tsv"
    bad.write_text("0.1\n0.2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scores rows"):
        evaluate_split(str(path), tmp_path, scores_path=bad)


def test_label_nunique_inconsistency_detected(tmp_path):
    path = tmp_path / "bad.tsv.gz"
    write_fixture(path, [row(SEED_TARGET, 1, "0", "UTR3")])
    report = evaluate_split(str(path), tmp_path)
    assert report["label_matches_nunique"] is False


def test_end_to_end_writes_json_and_md(fixture, tmp_path):
    path, scores = fixture
    out = tmp_path / "eval"
    code = main(["--split", str(path), "--data-dir", str(tmp_path),
                 "--scores", str(scores), "--out-dir", str(out)])
    assert code == 0
    files = list(out.glob("*.json"))
    assert files and (out / (files[0].stem + ".md")).exists()
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["metrics"]["auroc"] == 1.0


def test_split_table_marks_klimentova_not_external():
    assert "NOT" in " ".join(SPLITS["klimentova"]["warnings"])
    assert "unseen" in SPLITS["manakov_leftout"]["label"].lower()
