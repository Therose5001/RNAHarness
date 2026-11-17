"""Identifier and sequence normalisation.

Everything here is deliberately rule-based and explainable: when the agent
asks why `MIR21` resolved to `hsa-miR-21-5p`, the answer is a list of
candidate strings, not a model score.
"""
from __future__ import annotations

import hashlib
import re

# IUPAC nucleotide codes (RNA). Anything else is rejected before hashing.
_RNA_ALPHABET = set("ACGUNRYSWKMBDHV")
_DNA_ONLY = set("T")


class SequenceError(ValueError):
    pass


def normalize_sequence(sequence: str) -> str:
    """Uppercase, strip whitespace, DNA T -> RNA U. Rejects non-IUPAC."""
    seq = re.sub(r"\s+", "", sequence or "").upper().replace("T", "U")
    if not seq:
        raise SequenceError("empty sequence")
    bad = sorted(set(seq) - _RNA_ALPHABET)
    if bad:
        raise SequenceError(f"non-RNA characters: {''.join(bad)}")
    return seq


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(normalize_sequence(sequence).encode("ascii")).hexdigest()


def alias_key(text: str) -> str:
    """Lookup key for names. Case/space/underscore insensitive."""
    return re.sub(r"[\s_]+", "-", (text or "").strip().lower())


# hsa-miR-21-5p / mmu-miR-21a-3p / MIR21 / miR-21
_MIRNA_RE = re.compile(
    r"^(?:(?P<species>(?!(?:mir|mic|mim)(?:-|$))[a-z]{3})-)?"
    r"(?:mir|microrna|mimat)?-?"
    r"(?P<family>\d+[a-z]?)"
    r"(?:-(?P<arm>[35])p)?$",
    re.IGNORECASE,
)


def parse_mirna_name(name: str) -> dict:
    """Return the explainable parts of a miRNA surface form.

    A supplied species or arm is metadata, not permission to fall back to
    unrelated species or mature arms.
    """
    raw = (name or "").strip()
    match = _MIRNA_RE.match(raw)
    if not match:
        return {
            "raw": raw,
            "key": alias_key(raw),
            "species": None,
            "family": None,
            "arm": None,
            "explicit_species": False,
            "explicit_arm": False,
        }
    species = (match.group("species") or "").lower() or None
    arm = match.group("arm")
    return {
        "raw": raw,
        "key": alias_key(raw),
        "species": species,
        "family": match.group("family").lower(),
        "arm": f"{arm}p" if arm else None,
        "explicit_species": bool(species),
        "explicit_arm": bool(arm),
    }


def mirna_candidates(name: str) -> list[str]:
    """Candidate alias keys for a miRNA name, most specific first.

    Example: `hsa-miR-21-5p` -> hsa-mir-21-5p, hsa-mir-21, mir-21-5p,
    mir-21, mir21, mir-21a? (only when the family carries a letter).
    """
    raw = (name or "").strip()
    if not raw:
        return []
    candidates: list[str] = [alias_key(raw)]

    parsed = parse_mirna_name(raw)
    if parsed["family"] is None:
        return _dedupe(candidates)

    species = parsed["species"]
    family = parsed["family"]
    arm = parsed["arm"]

    def add(text: str) -> None:
        candidates.append(alias_key(text))

    base_forms = [f"mir-{family}", f"mir{family}", f"microrna-{family}"]
    if species:
        # Stay inside the explicitly named species. Without an explicit arm,
        # both mature arms are valid candidates and should surface ambiguity.
        for form in base_forms:
            add(f"{species}-{form}")
            for candidate_arm in ([arm] if arm else ["5p", "3p"]):
                add(f"{species}-{form}-{candidate_arm}")
    elif arm:
        # An arm-specific query must not broaden back to the family.
        for form in base_forms:
            add(f"{form}-{arm}")
    else:
        for form in base_forms:
            add(form)

    # MIR21 (HGNC style) is alias_key'ed to mir21 above; also try mir-21.
    return _dedupe(candidates)


def gene_candidates(symbol: str) -> list[str]:
    raw = (symbol or "").strip()
    if not raw:
        return []
    # Alias rows are stored under alias_key(), which is lowercase.
    return _dedupe([alias_key(raw)])


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
