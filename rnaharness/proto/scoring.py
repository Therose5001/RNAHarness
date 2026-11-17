"""Explainable stand-in for `rna_relation` / `rna_scan` (PLAN.md §3, §4).

Reuses `scripts/mirbench_eval.py:seed_match_score` so the prototype score is
exactly the published baseline (AUROC 0.677 on Manakov test). It is a COUNT,
not a calibrated probability: never present it as one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_EVAL = ROOT / "scripts" / "mirbench_eval.py"
_spec = importlib.util.spec_from_file_location("_rnaharness_mirbench_eval", _EVAL)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

seed_match_score = _mod.seed_match_score
roundtrip_rna = _mod.roundtrip_rna
reverse_complement_rna = _mod.reverse_complement_rna

WINDOW_NT = 50
MAX_NT = 1024
ALLOWED = set("ACGUacguTtNn")


def validate(seq: str, field: str, max_nt: int = MAX_NT) -> str:
    """Normalize T->U and reject clearly out-of-domain input (PLAN §3 OOD)."""
    s = (seq or "").strip().upper().replace("T", "U")
    if not s:
        raise ValueError(f"{field} is empty")
    if len(s) > max_nt:
        raise ValueError(f"{field} is {len(s)} nt, exceeds {max_nt}")
    bad = set(s) - set("ACGUN")
    if bad:
        raise ValueError(f"{field} contains non-RNA characters: {sorted(bad)}")
    return s


def seed_score(mirna: str, target: str, wobble: bool = False) -> dict:
    """One 50 nt window -> uncalibrated seed-match count + OOD flag."""
    mir = validate(mirna, "mirna")
    win = validate(target, "target")
    in_domain = len(win) == WINDOW_NT and len(mir) >= 8 and "N" not in win
    return {
        "score": seed_match_score(mir, win, wobble),
        "score_kind": "uncalibrated_seed_match_count",
        "definition": "2*count(seed7 in rc(target)) + 1*count(seed6)",
        "seed7_positions": "miRNA 2-8 (1-based)",
        "in_domain": in_domain,
        "window_nt": len(win),
        "mirna_nt": len(mir),
        "wobble": wobble,
        "model": "simple-algorithm-standin",
        "calibrated": False,
        "note": "This is an explainable count, not a probability. Do not treat "
                "0.87-style thresholds as applicable.",
    }


def scan_windows(mirna: str, target: str, window: int = WINDOW_NT,
                 step: int = 10, wobble: bool = False) -> dict:
    """PLAN §4 option A: tool-internal sliding window, max aggregation."""
    if window < 6 or step < 1:
        raise ValueError("window must be >= 6 and step >= 1")
    mir = validate(mirna, "mirna")
    seq = validate(target, "target")
    if len(seq) < window:
        windows = [(0, seq)]
    else:
        windows = [(i, seq[i:i + window])
                   for i in range(0, len(seq) - window + 1, step)]
        last = len(seq) - window
        if windows and windows[-1][0] != last:
            windows.append((last, seq[last:last + window]))
    per = []
    for offset, win in windows:
        s = seed_match_score(mir, win, wobble)
        per.append({"offset": offset, "score": s})
    best = max(per, key=lambda r: r["score"]) if per else {"offset": 0, "score": 0.0}
    return {
        "best_window": {"offset": best["offset"],
                        "sequence": seq[best["offset"]:best["offset"] + window]},
        "best_score": best["score"],
        "aggregation": "max",
        "n_windows": len(per),
        "per_window_scores": per,
        "window": window,
        "step": step,
        "in_domain": len(seq) >= window and all(
            len(w) == window for _, w in windows),
        "score_kind": "uncalibrated_seed_match_count",
        "calibrated": False,
        "model": "simple-algorithm-standin",
    }
