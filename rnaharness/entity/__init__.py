"""Entity layer (PLAN.md §1.2 P1).

Normalises sequences, names and identifiers BEFORE anything else runs, so the
rest of the harness never has to guess whether `miR-21`, `hsa-miR-21-5p` and
`MIR21` are the same thing.
"""
from .store import EntityStore, NotLoaded, default_db_path
from .naming import normalize_sequence, sequence_hash, mirna_candidates, alias_key

__all__ = [
    "EntityStore",
    "NotLoaded",
    "default_db_path",
    "normalize_sequence",
    "sequence_hash",
    "mirna_candidates",
    "alias_key",
]
