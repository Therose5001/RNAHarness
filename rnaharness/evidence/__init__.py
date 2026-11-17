"""Evidence layer (PLAN.md §6).

A local, reproducible index over literature passages plus pre-extracted
relations. Retrieval is entity-aware: names are resolved through the entity
store first, and a miss reports coverage — it is never phrased as "does not
exist".
"""
from .store import EvidenceSchemaError, EvidenceStore, NotLoaded, default_db_path
from .search import citation_verify, evidence_lookup, literature_search, resolve_pair

__all__ = [
    "EvidenceStore",
    "EvidenceSchemaError",
    "NotLoaded",
    "default_db_path",
    "citation_verify",
    "evidence_lookup",
    "literature_search",
    "resolve_pair",
]
