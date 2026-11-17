#!/usr/bin/env python3
"""Inventory the MiRBench AGO2 CLASH / eCLIP exports.

Streams the gzipped TSVs once per file and reports: row counts, label
balance, region/feature/strand coverage, entity counts, sequence-length
checks, Nunique-vs-label consistency, phyloP/phastCons coverage, and
train/test pair-level overlap (a leakage check).

    python scripts/mirbench_inventory.py --data-dir "C:/path/to/folder" \
        --out-dir reports

Writes <out-dir>/mirbench_inventory.json and .md. No data is modified.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

STANDARD_REQUIRED = ["gene", "noncodingRNA", "noncodingRNA_name", "label"]
# (train, test) pairs used for overlap checks; leftout is also checked.
OVERLAP_PAIRS = [
    ("AGO2_CLASH_Hejret2023_train.tsv.gz", ["AGO2_CLASH_Hejret2023_test.tsv.gz"]),
    ("AGO2_eCLIP_Manakov2022_train.tsv.gz",
     ["AGO2_eCLIP_Manakov2022_test.tsv.gz", "AGO2_eCLIP_Manakov2022_leftout.tsv.gz"]),
    # cross-source generalisation: is Klimentova (external) covered by Manakov train?
    ("AGO2_eCLIP_Manakov2022_train.tsv.gz", ["AGO2_eCLIP_Klimentova2022_test.tsv.gz"]),
]


def iter_tsv(path: Path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in fh:
            if not line.strip():
                continue
            yield header, idx, line.rstrip("\n").split("\t")


def is_standard(idx: dict) -> bool:
    return all(col in idx for col in STANDARD_REQUIRED)


def species_of(mirna_name: str) -> str:
    return mirna_name.split("-", 1)[0] if "-" in mirna_name else "?"


def top(counter: collections.Counter, n: int = 10) -> dict:
    return {str(k): v for k, v in counter.most_common(n)}


def scan_standard(path: Path, keep_pairs: bool) -> tuple[dict, dict[str, set[int]]]:
    labels = collections.Counter()
    regions = collections.Counter()
    features = collections.Counter()
    strands = collections.Counter()
    fams = collections.Counter()
    species = collections.Counter()
    win_len = collections.Counter()
    mir_len = collections.Counter()
    phylo_len = collections.Counter()
    phast_len = collections.Counter()
    cluster_count = collections.Counter()
    label_vs_nunique = collections.Counter()
    genes: set[str] = set()
    mirnas: set[str] = set()
    genes_windows = collections.Counter()
    pairs: set[int] = set()
    clusters_set: set[int] = set()
    mirnas_set: set[int] = set()

    rows = dup_pairs = 0
    missing = collections.Counter()
    header: list[str] = []
    for header, idx, f in iter_tsv(path):
        rows += 1
        get = lambda k, default="": f[idx[k]] if k in idx and idx[k] < len(f) else default
        label = get("label", "NA")
        labels[label] += 1
        regions[get("dominant_region", "NA")] += 1
        features[get("feature", "NA")] += 1
        strands[get("strand", "NA")] += 1
        fam = get("noncodingRNA_fam", "NA") or "unknown"
        fams[fam] += 1
        name = get("noncodingRNA_name")
        species[species_of(name)] += 1
        genes.add(get("gene"))
        mirnas.add(name)
        genes_windows[get("gene")] += 1
        win_len[len(get("gene"))] += 1
        mir_len[len(get("noncodingRNA"))] += 1
        phylo_len[len(get("gene_phyloP"))] += 1
        phast_len[len(get("gene_phastCons"))] += 1
        cluster = get("gene_cluster_ID")
        if cluster:
            cluster_count[cluster] += 1
            if keep_pairs:
                clusters_set.add(hash(cluster))
        if keep_pairs and name:
            mirnas_set.add(hash(name))
        nunique = get("Nunique")
        label_vs_nunique[(label, "Nunique>0" if nunique not in ("0", "") else "Nunique=0")] += 1
        for col in ("gene", "noncodingRNA_name", "label"):
            if not get(col):
                missing[col] += 1
        if keep_pairs:
            key = hash(name + "\t" + get("gene"))
            if key in pairs:
                dup_pairs += 1
            else:
                pairs.add(key)

    stats = {
        "rows": rows,
        "columns": header,
        "labels": dict(labels),
        "label_balance": {k: round(v / rows, 4) for k, v in labels.items()} if rows else {},
        "dominant_region": top(regions, 20),
        "feature": top(features, 20),
        "strand": dict(strands),
        "species": dict(species),
        "unique_mirnas": len(mirnas),
        "unique_genes": len(genes),
        "unique_gene_clusters": len(cluster_count),
        "windows_per_gene": {
            "max": max(genes_windows.values()) if genes_windows else 0,
            "mean": round(sum(genes_windows.values()) / len(genes_windows), 2) if genes_windows else 0,
            "genes_with_1": sum(1 for v in genes_windows.values() if v == 1),
        },
        "target_length": dict(sorted(win_len.items())),
        "mirna_length": dict(sorted(mir_len.items())),
        "phyloP_field_len": dict(sorted(phylo_len.items())),
        "phastCons_field_len": dict(sorted(phast_len.items())),
        "known_families_top": top(fams, 15),
        "unknown_family_rows": fams.get("unknown", 0),
        "label_vs_nunique": {f"{k[0]}|{k[1]}": v for k, v in label_vs_nunique.items()},
        "duplicate_pairs": dup_pairs,
        "duplicate_pair_rate": round(dup_pairs / rows, 4) if rows else 0,
        "missing_fields": dict(missing),
    }
    return stats, {"pairs": pairs, "clusters": clusters_set, "mirnas": mirnas_set}


def scan_full(path: Path) -> dict:
    """Manakov full_dataset has a different, label-free schema."""
    types = collections.Counter()
    fams = collections.Counter()
    repeats = collections.Counter()
    seq_len = collections.Counter()
    mir_len = collections.Counter()
    genes: set[str] = set()
    mirnas: set[str] = set()
    rows = 0
    header: list[str] = []
    for header, idx, f in iter_tsv(path):
        rows += 1
        get = lambda k, default="": f[idx[k]] if k in idx and idx[k] < len(f) else default
        types[get("noncodingRNA_type", "NA")] += 1
        fam = get("noncodingRNA_fam", "NA") or "unknown"
        if fam != "unknown":
            fams[fam] += 1
        rep = get("repeat_fam", "")
        if rep:
            repeats[rep] += 1
        seq_len[len(get("seq.g"))] += 1
        mir_len[len(get("noncodingRNA_real_seq"))] += 1
        genes.add(get("gene_name"))
        mirnas.add(get("noncodingRNA"))
    return {
        "rows": rows,
        "columns": header,
        "has_label": "label" in header,
        "noncodingRNA_type": dict(types),
        "known_families_top": top(fams, 15),
        "top_repeat_families": top(repeats, 10),
        "unique_mirnas": len(mirnas),
        "unique_genes": len(genes),
        "seq_g_length": dict(sorted(seq_len.items())[:10]),
        "mirna_length": dict(sorted(mir_len.items())[:10]),
    }


def compare_overlap(train_path: Path, test_paths: list[Path]) -> list[dict]:
    _, train_sets = scan_standard(train_path, keep_pairs=True)
    out = []
    for test_path in test_paths:
        _, test_sets = scan_standard(test_path, keep_pairs=True)
        pair_inter = len(train_sets["pairs"] & test_sets["pairs"])
        cluster_inter = len(train_sets["clusters"] & test_sets["clusters"])
        mirna_inter = len(train_sets["mirnas"] & test_sets["mirnas"])
        out.append({
            "train": train_path.name,
            "test": test_path.name,
            "train_pairs": len(train_sets["pairs"]),
            "test_pairs": len(test_sets["pairs"]),
            "overlap_pairs": pair_inter,
            "overlap_rate_of_test": round(pair_inter / len(test_sets["pairs"]), 4)
            if test_sets["pairs"] else 0,
            "test_gene_clusters": len(test_sets["clusters"]),
            "overlap_gene_clusters": cluster_inter,
            "gene_cluster_overlap_rate": round(cluster_inter / len(test_sets["clusters"]), 4)
            if test_sets["clusters"] else 0,
            "test_mirnas": len(test_sets["mirnas"]),
            "overlap_mirnas": mirna_inter,
            "mirna_overlap_rate": round(mirna_inter / len(test_sets["mirnas"]), 4)
            if test_sets["mirnas"] else 0,
        })
    return out


def _pct_map(counter: dict, total: int, n: int = 6) -> str:
    items = sorted(counter.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(f"{k}: {v:,} ({v / total:.1%})" for k, v in items) if total else "-"


def _len_summary(dist: dict) -> str:
    if not dist:
        return "-"
    ints = {int(k): v for k, v in dist.items()}
    top = sorted(ints.items(), key=lambda kv: -kv[1])[:3]
    top_txt = ", ".join(f"{k}×{v:,}" for k, v in top)
    return f"{min(ints)}-{max(ints)} (top: {top_txt})"


def render_markdown(report: dict) -> str:
    lines = ["# MiRBench inventory", "",
             f"Generated {report['generated_at']}", "",
             "## Files", "",
             "| file | rows | labels | unique miRNA | unique genes | 50 nt targets |",
             "|---|---:|---|---:|---:|---:|"]
    for name, s in report["files"].items():
        if "rows" not in s:
            continue
        labels = s.get("labels") or {}
        labels_txt = ", ".join(f"{k}:{v:,}" for k, v in labels.items()) or "-"
        target50 = s.get("target_length", {}).get("50", 0)
        lines.append(f"| {name} | {s['rows']:,} | {labels_txt} | "
                     f"{s.get('unique_mirnas', 0):,} | {s.get('unique_genes', 0):,} | {target50:,} |")

    lines += ["", "## Per-file detail", ""]
    for name, s in report["files"].items():
        lines.append(f"### {name}")
        lines.append("")
        if s.get("has_label") is False:
            lines.append(f"- rows: {s['rows']:,}; **no title label column** (raw dataset)")
            lines.append(f"- noncodingRNA types: {s.get('noncodingRNA_type')}")
            lines.append(f"- unique miRNAs / genes: {s.get('unique_mirnas', 0):,} / "
                         f"{s.get('unique_genes', 0):,}")
            lines.append(f"- top repeat families: {s.get('top_repeat_families')}")
            lines.append("")
            continue
        total = s["rows"] or 1
        lines.append(f"- rows: {s['rows']:,}; columns: {len(s.get('columns', []))}")
        lines.append(f"- labels: {s.get('labels')} "
                     f"(share {s.get('label_balance')})")
        lines.append(f"- dominant region: {_pct_map(s.get('dominant_region', {}), total)}")
        lines.append(f"- feature: {_pct_map(s.get('feature', {}), total, 4)}")
        lines.append(f"- strand: {s.get('strand')}; species: {s.get('species')}")
        lines.append(f"- unique miRNA / genes / gene clusters: {s.get('unique_mirnas', 0):,} / "
                     f"{s.get('unique_genes', 0):,} / {s.get('unique_gene_clusters', 0):,}")
        lines.append(f"- windows per gene: {s.get('windows_per_gene')}")
        lines.append(f"- target length: {_len_summary(s.get('target_length', {}))}")
        lines.append(f"- miRNA length: {_len_summary(s.get('mirna_length', {}))}")
        lines.append(f"- label × Nunique: {s.get('label_vs_nunique')}")
        lines.append(f"- known-family rows: {total - s.get('unknown_family_rows', 0):,}; "
                     f"top: {s.get('known_families_top')}")
        lines.append(f"- duplicate (miRNA, target) pairs: {s.get('duplicate_pairs', 0):,} "
                     f"({s.get('duplicate_pair_rate', 0):.2%})")
        lines.append(f"- phyloP/phastCons field lengths: "
                     f"{_len_summary(s.get('phyloP_field_len', {}))} / "
                     f"{_len_summary(s.get('phastCons_field_len', {}))}")
        if s.get("missing_fields"):
            lines.append(f"- missing fields: {s['missing_fields']}")
        lines.append("")

    lines += ["## Overlap (leakage check)", "",
              "Pair overlap = identical (miRNA, target 50-mer). Cluster overlap =",
              "same `gene_cluster_ID`; a non-zero value is not sequence leakage but",
              "means the same genomic region is represented in both splits.", "",
              "| train | test | pair overlap | rate | gene-cluster overlap | rate | miRNA overlap | rate |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for o in report["overlaps"]:
        lines.append(
            f"| {o['train']} | {o['test']} | {o['overlap_pairs']:,} | "
            f"{o['overlap_rate_of_test']:.2%} | {o['overlap_gene_clusters']:,} / "
            f"{o['test_gene_clusters']:,} | {o['gene_cluster_overlap_rate']:.2%} | "
            f"{o['overlap_mirnas']:,} / {o['test_mirnas']:,} | {o['mirna_overlap_rate']:.2%} |")
    lines += ["", "## Reading notes", ""]
    labeled = [n for n, s in report["files"].items() if "labels" in s]
    if labeled:
        all_nunique = all(
            set(report["files"][n].get("label_vs_nunique", {})) <=
            {"0|Nunique=0", "1|Nunique>0"}
            for n in labeled
        )
        lines.append("- **Label definition**: " + (
            "`label == (Nunique > 0)` in every split — positives are windows with "
            "at least one AGO2 read, negatives are zero-read windows. AUROC on this "
            "data measures CLIP-evidence prediction, not curated relationship truth."
            if all_nunique else "labels are not simply Nunique>0; inspect per file."))
    all_pairs_disjoint = True
    for o in report["overlaps"]:
        pair_rate = o["overlap_rate_of_test"]
        if pair_rate:
            all_pairs_disjoint = False
        loci = "same" if o.get("gene_cluster_overlap_rate", 0) > 0.5 else "new"
        if "leftout" in o["test"] and o.get("mirna_overlap_rate", 0) == 0:
            lines.append(
                f"- **{o['test']} is miRNA-disjoint from train** "
                f"({o['test_mirnas']} miRNAs, none seen in training) while "
                f"{o['gene_cluster_overlap_rate']:.0%} of loci overlap — use it to "
                "measure generalisation to unseen miRNAs, not unseen loci.")
        else:
            lines.append(
                f"- **{o['test']}**: reuses {o['mirna_overlap_rate']:.1%} of train "
                f"miRNAs, {o['gene_cluster_overlap_rate']:.1%} of its gene clusters, "
                f"and {pair_rate:.1%} of its exact (miRNA, target) pairs are in train "
                f"— {loci}-locus test set.")
    if report["overlaps"]:
        lines.append(
            "- Within-source train/test splits are exactly pair-disjoint."
            if all_pairs_disjoint else
            "- Exact pair overlap is not zero everywhere; the Klimentova/Manakov "
            "overlap means Klimentova is **not** an independent external test set.")
    lines += ["", "## Warnings", ""]
    for w in report["warnings"]:
        lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", default="reports")
    ap.add_argument("--only", nargs="*", help="limit to these file names")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(data_dir.glob("*.tsv.gz"))
    if args.only:
        wanted = set(args.only)
        files = [f for f in files if f.name in wanted]
    if not files:
        print(f"no .tsv.gz files in {data_dir}", file=sys.stderr)
        return 1

    report: dict = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "data_dir": str(data_dir), "files": {}, "overlaps": [],
                    "warnings": []}
    for path in files:
        print(f"[scan] {path.name}", file=sys.stderr, flush=True)
        if path.name.endswith("full_dataset.tsv.gz"):
            report["files"][path.name] = scan_full(path)
        else:
            stats, _ = scan_standard(path, keep_pairs=False)
            report["files"][path.name] = stats

    for train_name, test_names in OVERLAP_PAIRS:
        train = data_dir / train_name
        tests = [data_dir / n for n in test_names]
        if not train.exists() or not all(t.exists() for t in tests):
            continue
        print(f"[overlap] {train_name}", file=sys.stderr, flush=True)
        report["overlaps"].extend(compare_overlap(train, tests))

    # Warnings that matter for modelling.
    for name, s in report["files"].items():
        if "labels" in s:
            total = sum(s["labels"].values())
            if total and abs(s["labels"].get("0", 0) - s["labels"].get("1", 0)) / total > 0.9:
                report["warnings"].append(f"{name}: extreme label imbalance {s['labels']}")
            if s.get("target_length") and set(s["target_length"]) != {50}:
                report["warnings"].append(
                    f"{name}: target lengths {sorted(s['target_length'])} (model expects 50 nt)")
            if s.get("missing_fields"):
                report["warnings"].append(f"{name}: missing fields {s['missing_fields']}")
        elif s.get("has_label") is False:
            report["warnings"].append(
                f"{name}: no `label` column — raw full dataset, not a train/test split")
    for o in report["overlaps"]:
        if o["overlap_rate_of_test"] > 0.01:
            report["warnings"].append(
                f"{o['train']} <-> {o['test']}: pair overlap {o['overlap_rate_of_test']:.1%} "
                "(possible leakage — inspect before using as a clean test set)")
        if o.get("gene_cluster_overlap_rate", 0) > 0.01:
            report["warnings"].append(
                f"{o['train']} <-> {o['test']}: gene-cluster overlap "
                f"{o['gene_cluster_overlap_rate']:.1%} — same loci appear in both splits "
                "(sequence-level disjoint, locus-level not)")

    json_path = out_dir / "mirbench_inventory.json"
    md_path = out_dir / "mirbench_inventory.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"wrote {json_path} and {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
