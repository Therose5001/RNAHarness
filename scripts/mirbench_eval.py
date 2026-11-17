#!/usr/bin/env python3
"""Evaluate a miRNA-target scorer on MiRBench splits.

Splits are not interchangeable (see reports/mirbench_inventory.md):

  manakov_test     seen miRNAs, mostly new loci        window-level generalisation
  manakov_leftout  miRNA-disjoint, 89% same loci       unseen-miRNA generalisation
  clash_test       different protocol, 1,018 rows      cross-protocol
  klimentova       NOT independent of Manakov train    same-locus replication

`label == (Nunique > 0)` in every labelled file: positives are windows with at
least one AGO2 read, negatives are zero-read windows. AUROC here measures
CLIP-evidence prediction, not curated relationship truth.

Usage:
    python scripts/mirbench_eval.py --split manakov_test --region UTR3
    python scripts/mirbench_eval.py --split clash_test --scores scores.tsv
    python scripts/mirbench_eval.py --split manakov_leftout --dry-run

`--scores` is a TSV with either `label<TAB>score` or one `score` per row, in
the same order as the *unfiltered* split file. Without it, the explainable
seed-match baseline is used.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import json
import math
import sys
from pathlib import Path

DEFAULT_DATA_DIR = Path(r"C:\baidunetdiskdownload\新建文件夹")

SPLITS: dict[str, dict] = {
    "manakov_test": {
        "file": "AGO2_eCLIP_Manakov2022_test.tsv.gz",
        "label": "seen miRNAs / new loci",
        "semantics": "window-level generalisation; 99.7% miRNAs and 3.5% gene "
                     "clusters also appear in Manakov train, exact pairs disjoint",
        "warnings": [],
    },
    "manakov_leftout": {
        "file": "AGO2_eCLIP_Manakov2022_leftout.tsv.gz",
        "label": "unseen miRNAs / same loci",
        "semantics": "miRNA-disjoint from train (0/336 miRNAs), 89.4% of gene "
                     "clusters shared; measures generalisation to unseen miRNAs",
        "warnings": ["same loci as train: do not report it as a new-locus test"],
    },
    "clash_test": {
        "file": "AGO2_CLASH_Hejret2023_test.tsv.gz",
        "label": "cross-protocol",
        "semantics": "CLASH, 1,018 rows; 99.6% miRNAs seen in CLASH train, "
                     "1.3% gene-cluster overlap; small, so report CIs or raw counts",
        "warnings": ["small test set (1,018 rows): metric noise is large"],
    },
    "klimentova": {
        "file": "AGO2_eCLIP_Klimentova2022_test.tsv.gz",
        "label": "same-locus replication, NOT external",
        "semantics": "96.8% miRNAs, 94.2% gene clusters and 3.0% exact pairs "
                     "overlap Manakov train; not an independent external test set",
        "warnings": ["NOT an independent external test set — never report it as "
                     "external validation"],
    },
}

COMPLEMENT = str.maketrans("ACGUacgu", "UGCAugca")


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def _average_ranks(scores: list[float]) -> list[float]:
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def auroc(labels: list[int], scores: list[float]) -> float | None:
    """Mann-Whitney AUROC with average ranks for ties. None if one class only."""
    positives = sum(1 for y in labels if y == 1)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ranks = _average_ranks(scores)
    rank_sum = sum(r for r, y in zip(ranks, labels) if y == 1)
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def average_precision(labels: list[int], scores: list[float]) -> float | None:
    """Step-wise AP (not trapezoid); ties form one threshold group."""
    positives = sum(1 for y in labels if y == 1)
    if positives == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    tp = fp = 0
    prev_recall = 0.0
    ap = 0.0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            if labels[order[k]] == 1:
                tp += 1
            else:
                fp += 1
        recall = tp / positives
        precision = tp / (tp + fp)
        ap += (recall - prev_recall) * precision
        prev_recall = recall
        i = j + 1
    return ap


def ece(labels: list[int], scores: list[float], bins: int = 10) -> dict | None:
    """Expected calibration error for scores in [0, 1]."""
    if any(s < 0.0 or s > 1.0 or math.isnan(s) for s in scores):
        return None
    buckets: list[list[tuple[int, float]]] = [[] for _ in range(bins)]
    for y, s in zip(labels, scores):
        idx = min(int(s * bins), bins - 1)
        buckets[idx].append((y, s))
    total = len(labels)
    value = 0.0
    table = []
    for idx, bucket in enumerate(buckets):
        if not bucket:
            continue
        conf = sum(s for _, s in bucket) / len(bucket)
        acc = sum(y for y, _ in bucket) / len(bucket)
        value += len(bucket) / total * abs(acc - conf)
        table.append({"bin": [round(idx / bins, 2), round((idx + 1) / bins, 2)],
                      "n": len(bucket), "confidence": round(conf, 4),
                      "accuracy": round(acc, 4)})
    return {"value": value, "bins": table}


# --------------------------------------------------------------------------
# seed-match baseline
# --------------------------------------------------------------------------
def roundtrip_rna(seq: str) -> str:
    return seq.strip().upper().replace("T", "U")


def reverse_complement_rna(seq: str) -> str:
    return roundtrip_rna(seq).translate(COMPLEMENT)[::-1]


def _count(seq: str, sub: str) -> int:
    if not sub:
        return 0
    count = 0
    start = 0
    while True:
        idx = seq.find(sub, start)
        if idx < 0:
            return count
        count += 1
        start = idx + 1


def seed_match_score(mirna: str, target: str, wobble: bool = False) -> float:
    """2 * (7-mer hits at miRNA 2-8) + 1 * (6-mer hits at 2-7) in the target."""
    mir = roundtrip_rna(mirna)
    rc = reverse_complement_rna(target)
    if len(mir) < 8 or len(rc) < 6:
        return 0.0
    seed7 = mir[1:8]
    seed6 = mir[1:7]
    hits7 = _count(rc, seed7)
    hits6 = _count(rc, seed6)
    if wobble:
        for offset in (0, 5):  # seed positions 1 and 6 (0-based within seed)
            if seed7[offset] == "G":
                variant = seed7[:offset] + "U" + seed7[offset + 1:]
                hits7 += _count(rc, variant)
    return 2.0 * hits7 + 1.0 * hits6


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8", errors="replace")


def read_header(path: Path) -> list[str]:
    with _open(path) as fh:
        return fh.readline().rstrip("\n").split("\t")


def iter_rows(path: Path):
    with _open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        if "label" not in idx:
            raise ValueError(f"{path.name} has no `label` column (raw, unlabelled file)")
        for line in fh:
            if not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            yield idx, f


def load_scores(path: Path) -> list[float | None]:
    """Accept one score per line, or `label<TAB>score`; a text header is skipped."""
    scores: list[float | None] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.strip().split("\t")
            if not parts or not parts[-1]:
                continue
            value = parts[-1]
            if not _is_float(value):
                if value.lower() in ("score", "scores", "pred", "probability"):
                    continue
                scores.append(None)
                continue
            scores.append(float(value))
    return scores


def _is_float(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def evaluate_split(split: str, data_dir: Path, regions: set[str] | None = None,
                   scores_path: Path | None = None, wobble: bool = False,
                   bins: int = 10) -> dict:
    meta = SPLITS.get(split) or {"file": split, "label": split, "semantics": "",
                                 "warnings": []}
    path = data_dir / meta["file"]
    if not path.exists():
        raise FileNotFoundError(path)

    file_scores = load_scores(scores_path) if scores_path else None
    labels: list[int] = []
    used_scores: list[float] = []
    region_counts: collections.Counter = collections.Counter()
    rows_total = rows_used = 0
    label_matches_nunique = True

    for idx, f in iter_rows(path):
        rows_total += 1
        get = lambda k: f[idx[k]] if k in idx and idx[k] < len(f) else ""
        region = get("dominant_region")
        region_counts[region] += 1
        if regions and region not in regions:
            continue
        try:
            label = int(get("label"))
        except ValueError:
            continue
        nunique = get("Nunique")
        if nunique and label != (0 if nunique in ("0", "") else 1):
            label_matches_nunique = False
        row_no = rows_total - 1
        if file_scores is not None:
            if row_no >= len(file_scores):
                raise ValueError(
                    f"scores rows ({len(file_scores)}) < split rows; "
                    "scores must align with the unfiltered split file")
            score = file_scores[row_no]
            if score is None:
                continue
        else:
            score = seed_match_score(get("noncodingRNA"), get("gene"), wobble)
        labels.append(label)
        used_scores.append(float(score))
        rows_used += 1

    if file_scores is not None and len(file_scores) != rows_total:
        raise ValueError(
            f"scores rows ({len(file_scores)}) != split rows ({rows_total}); "
            "scores must align with the unfiltered split file")

    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "split": split,
        "file": str(path),
        "split_label": meta["label"],
        "split_semantics": meta["semantics"],
        "score_source": "file" if file_scores is not None else "seed_match",
        "seed_match": None if file_scores is not None else {
            "definition": "2*count(seed7 in rc(target)) + 1*count(seed6)",
            "seed7_positions": "miRNA 2-8 (1-based)",
            "seed6_positions": "miRNA 2-7 (1-based)",
            "wobble": wobble,
        },
        "filters": {"regions": sorted(regions) if regions else None},
        "rows_total": rows_total,
        "rows_used": rows_used,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "prevalence": round(n_pos / rows_used, 6) if rows_used else None,
        "metrics": {
            "auroc": auroc(labels, used_scores),
            "average_precision": average_precision(labels, used_scores),
            "ece": ece(labels, used_scores, bins),
        },
        "region_counts": dict(region_counts),
        "label_matches_nunique": label_matches_nunique,
        "warnings": list(meta.get("warnings", [])),
    }
    if regions and rows_used < rows_total:
        report["warnings"].append(
            f"region filter kept {rows_used:,}/{rows_total:,} rows")
    if not label_matches_nunique:
        report["warnings"].append("label != (Nunique>0) on at least one row")
    if report["metrics"]["ece"] is None:
        report["warnings"].append("ECE skipped: scores outside [0,1]")
    return report


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def render_markdown(report: dict) -> str:
    m = report["metrics"]
    ece_part = m["ece"]["value"] if m["ece"] else None
    lines = [
        f"# MiRBench evaluation — {report['split']}",
        "",
        f"- file: `{report['file']}`",
        f"- split: **{report['split_label']}**",
        f"- semantics: {report['split_semantics']}",
        f"- score source: **{report['score_source']}**",
        f"- rows used: {report['rows_used']:,} / {report['rows_total']:,}",
        f"- positives/negatives: {report['n_pos']:,} / {report['n_neg']:,} "
        f"(prevalence {report['prevalence']:.3%})",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| AUROC | {_fmt(m['auroc'])} |",
        f"| Average precision (AUPRC) | {_fmt(m['average_precision'])} |",
        f"| ECE (10 bins) | {_fmt(ece_part)} |",
        "",
        f"- label == (Nunique>0): {report['label_matches_nunique']}",
        f"- filters: {report['filters']}",
        f"- regions: {report['region_counts']}",
    ]
    if report.get("seed_match"):
        lines += ["", f"- seed-match baseline: {report['seed_match']}"]
    if report["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in report["warnings"]]
    return "\n".join(lines) + "\n"


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--split", required=True,
                    help=", ".join(sorted(SPLITS)) + ", or a raw .tsv.gz path")
    ap.add_argument("--region", default="", help="comma-separated dominant_region values")
    ap.add_argument("--scores", help="TSV: label<TAB>score or one score per row")
    ap.add_argument("--out-dir", default="reports/eval")
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--wobble", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    alias = {"3UTR": "UTR3", "5UTR": "UTR5", "UTR'3": "UTR3", "UTR3'": "UTR3"}
    regions = {alias.get(r.strip().upper(), r.strip().upper())
               for r in args.region.split(",") if r.strip()} or None
    data_dir = Path(args.data_dir)
    scores_path = Path(args.scores) if args.scores else None

    if args.dry_run:
        meta = SPLITS.get(args.split) or {"file": args.split}
        path = data_dir / meta["file"]
        rows = sum(1 for _ in iter_rows(path))
        print(json.dumps({"split": args.split, "file": str(path), "rows": rows},
                         ensure_ascii=False))
        return 0

    report = evaluate_split(args.split, data_dir, regions, scores_path,
                            args.wobble, args.bins)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.split.replace("/", "_").replace("\\", "_").removesuffix(".tsv.gz")
    if regions:
        stem += "__region-" + "-".join(sorted(regions))
    (out_dir / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("split", "score_source", "rows_used", "n_pos", "n_neg",
                       "metrics", "warnings")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
