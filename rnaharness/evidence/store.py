"""SQLite + FTS5 evidence store.

Tables: documents, passages, relations, meta. Passages are searchable through
FTS5 when available; otherwise the store falls back to LIKE and records
`search_mode=like` in meta. No embeddings here — BM25 is the P2 baseline.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from ..entity.store import NotLoaded  # one NotLoaded type across the harness

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "evidence.sqlite"
DB_ENV = "RNAHARNESS_EVIDENCE_DB"
SCHEMA_VERSION = 2

V2_RELATION_FIELDS = {
    "species",
    "tax_id",
    "mirna_arm",
    "polarity",
    "experimental_method",
    "curation_status",
    "extraction_method",
}
RELATION_FIELDS = [
    "relation_id", "mirna_id", "gene_id", "relation_type", "method",
    "doc_id", "passage_id", "confidence", "extracted_by", "species",
    "tax_id", "mirna_arm", "polarity", "experimental_method",
    "curation_status", "extraction_method",
]


class EvidenceSchemaError(ValueError):
    """A write requires relation columns that the existing store lacks."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id   TEXT PRIMARY KEY,
  pmid     TEXT,
  pmcid    TEXT,
  doi      TEXT,
  title    TEXT,
  journal  TEXT,
  year     INTEGER,
  license  TEXT,
  snapshot TEXT,
  source   TEXT
);
CREATE INDEX IF NOT EXISTS documents_pmid ON documents (pmid);

CREATE TABLE IF NOT EXISTS passages (
  passage_id TEXT PRIMARY KEY,
  doc_id     TEXT NOT NULL REFERENCES documents (doc_id) ON DELETE CASCADE,
  section    TEXT,
  text       TEXT NOT NULL,
  char_start INTEGER,
  char_end   INTEGER
);
CREATE INDEX IF NOT EXISTS passages_doc ON passages (doc_id);

CREATE TABLE IF NOT EXISTS relations (
  relation_id  TEXT PRIMARY KEY,
  mirna_id     TEXT NOT NULL,
  gene_id      TEXT NOT NULL,
  relation_type TEXT,
  method       TEXT,
  doc_id       TEXT,
  passage_id   TEXT,
  confidence   TEXT,
  extracted_by TEXT,
  species      TEXT,
  tax_id       TEXT,
  mirna_arm    TEXT,
  polarity     TEXT NOT NULL DEFAULT 'unknown',
  experimental_method TEXT,
  curation_status TEXT NOT NULL DEFAULT 'unreviewed',
  extraction_method TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS relations_pair ON relations (mirna_id, gene_id);
CREATE INDEX IF NOT EXISTS relations_gene ON relations (gene_id);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts
  USING fts5(passage_id UNINDEXED, text, tokenize = 'unicode61');
"""

_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9:\-]*")


def default_db_path() -> Path:
    return Path(os.environ.get(DB_ENV) or DEFAULT_DB)


def fts_query(terms: list[str]) -> str:
    """Build a safe FTS5 OR-query from free text; returns '' if no terms."""
    seen: list[str] = []
    for term in terms:
        for token in _TERM.findall(term or ""):
            if len(token) > 1 and token not in seen:
                seen.append(token)
    return " OR ".join(f'"{t}"' for t in seen)


class EvidenceStore:
    def __init__(self, path: Path, conn: sqlite3.Connection, fts: bool,
                 read_only: bool = False):
        self.path = path
        self.conn = conn
        self.fts = fts
        self.read_only = read_only

    @classmethod
    def open(cls, path: Path | str | None = None, create: bool = False) -> "EvidenceStore":
        target = Path(path) if path else default_db_path()
        exists = target.exists()
        needs_schema = not exists or target.stat().st_size == 0
        if needs_schema and not create:
            raise NotLoaded(target, "build it with scripts/evidence_ingest.py")
        if create:
            target.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(target))
        else:
            conn = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if create:
            conn.execute("PRAGMA foreign_keys = ON")
            if needs_schema:
                conn.executescript(SCHEMA)
                try:
                    conn.executescript(FTS_SCHEMA)
                    fts = True
                except sqlite3.OperationalError:
                    fts = False
                store = cls(target, conn, fts, read_only=False)
                store.set_meta("schema_version", str(SCHEMA_VERSION))
                store.set_meta("search_mode", "fts5" if fts else "like")
                conn.commit()
                return store
            fts = cls._has_fts(conn)
            return cls(target, conn, fts, read_only=False)

        conn.execute("PRAGMA query_only = ON")
        fts = cls._has_fts(conn)
        return cls(target, conn, fts, read_only=True)

    @staticmethod
    def _has_fts(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'passages_fts' LIMIT 1").fetchone()
        return row is not None

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # -- writes --------------------------------------------------------
    def upsert_document(self, doc: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO documents
              (doc_id, pmid, pmcid, doi, title, journal, year, license, snapshot, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (doc_id) DO UPDATE SET
              pmid=excluded.pmid, pmcid=excluded.pmcid, doi=excluded.doi,
              title=excluded.title, journal=excluded.journal, year=excluded.year,
              license=excluded.license, snapshot=excluded.snapshot,
              source=excluded.source
            """,
            (doc["doc_id"], doc.get("pmid"), doc.get("pmcid"), doc.get("doi"),
             doc.get("title"), doc.get("journal"), doc.get("year"),
             doc.get("license"), doc.get("snapshot"), doc.get("source")),
        )

    def add_passage(self, passage_id: str, doc_id: str, section: str,
                    text: str, char_start: int | None = None,
                    char_end: int | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO passages (passage_id, doc_id, section, text, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (passage_id) DO UPDATE SET
              doc_id=excluded.doc_id, section=excluded.section, text=excluded.text,
              char_start=excluded.char_start, char_end=excluded.char_end
            """,
            (passage_id, doc_id, section, text, char_start, char_end),
        )
        if self.fts:
            self.conn.execute("DELETE FROM passages_fts WHERE passage_id = ?",
                              (passage_id,))
            self.conn.execute(
                "INSERT INTO passages_fts (passage_id, text) VALUES (?, ?)",
                (passage_id, text))

    def _relation_columns(self) -> set[str]:
        return {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(relations)")
        }

    def validate_relation(self, rel: dict) -> set[str]:
        """Validate relation fields without mutating the database.

        Schema v1 remains readable and can still accept its original fields.
        Any attempt to persist schema-v2 metadata into a store without those
        columns fails before SQL execution so callers cannot silently lose
        species, polarity, or provenance.
        """
        available = self._relation_columns()
        requested_v2 = sorted(
            field for field in V2_RELATION_FIELDS if field in rel
        )
        missing_v2 = sorted(set(requested_v2) - available)
        if missing_v2:
            raise EvidenceSchemaError(
                "evidence store schema v1 cannot persist v2 relation metadata "
                f"({', '.join(missing_v2)}); build a new schema v2 database "
                "or migrate explicitly. Legacy stores remain readable, but "
                "no v2 relation was written."
            )
        missing_required = [
            field for field in ("relation_id", "mirna_id", "gene_id")
            if not rel.get(field)
        ]
        if missing_required:
            raise ValueError(
                "relation is missing required fields: "
                + ", ".join(missing_required)
            )
        return available

    def preflight_relations(self, relations: list[dict]) -> None:
        """Validate a relation batch before any document or relation write."""
        for relation in relations:
            self.validate_relation(relation)

    def require_v2_relation_support(self) -> None:
        """Fail early when an ingestion path is about to emit v2 relations."""
        missing = sorted(V2_RELATION_FIELDS - self._relation_columns())
        if missing:
            raise EvidenceSchemaError(
                "this ingestion path emits schema v2 relation metadata "
                f"({', '.join(missing)}) but the target evidence store is "
                "schema v1; build a new schema v2 database or migrate "
                "explicitly. No documents or relations were written."
            )

    def add_relation(self, rel: dict) -> None:
        available = self.validate_relation(rel)
        fields = [name for name in RELATION_FIELDS if name in available]
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in fields
            if name != "relation_id")
        defaults = {
            "polarity": "unknown",
            "curation_status": "unreviewed",
            "extraction_method": "unknown",
        }
        values = [rel.get(name, defaults.get(name)) for name in fields]
        self.conn.execute(
            f"INSERT INTO relations ({', '.join(fields)}) "
            f"VALUES ({placeholders}) ON CONFLICT (relation_id) DO UPDATE SET "
            f"{updates}",
            values,
        )

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    # -- reads ---------------------------------------------------------
    def _rows(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def get_document(self, doc_id_or_pmid: str) -> dict | None:
        rows = self._rows(
            "SELECT * FROM documents WHERE doc_id = ? OR pmid = ? LIMIT 1",
            (doc_id_or_pmid, doc_id_or_pmid))
        return rows[0] if rows else None

    def passages_for(self, doc_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM passages WHERE doc_id = ? ORDER BY passage_id", (doc_id,))

    def get_passage(self, passage_id: str) -> dict | None:
        rows = self._rows(
            """
            SELECT p.*, d.pmid, d.pmcid, d.title, d.snapshot
            FROM passages p JOIN documents d ON d.doc_id = p.doc_id
            WHERE p.passage_id = ?
            """,
            (passage_id,))
        return rows[0] if rows else None

    def search_passages(self, query: str, limit: int = 20) -> list[dict]:
        terms = fts_query([query])
        if not terms:
            return []
        if self.fts:
            try:
                return self._rows(
                    """
                    SELECT p.*, d.pmid, d.pmcid, d.title, d.snapshot,
                           bm25(passages_fts) AS rank
                    FROM passages_fts f
                    JOIN passages p ON p.passage_id = f.passage_id
                    JOIN documents d ON d.doc_id = p.doc_id
                    WHERE passages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (terms, limit))
            except sqlite3.OperationalError:
                pass  # malformed query or FTS unavailable: fall through
        tokens = [t for t in _TERM.findall(query)]
        if not tokens:
            return []
        like = "%" + tokens[0] + "%"
        return self._rows(
            """
            SELECT p.*, d.pmid, d.pmcid, d.title, d.snapshot, 0.0 AS rank
            FROM passages p JOIN documents d ON d.doc_id = p.doc_id
            WHERE p.text LIKE ?
            ORDER BY p.passage_id LIMIT ?
            """,
            (like, limit))

    def relations_for(self, mirna_ids: list[str], gene_ids: list[str],
                      *, mirna_aliases: list[str] | None = None,
                      gene_aliases: list[str] | None = None) -> list[dict]:
        """Return candidate rows; callers must validate canonical identity.

        Alias candidates keep legacy surface-ID rows visible. They do not make
        a relation applicable: search resolves each endpoint and applies the
        same species, arm, document, and passage checks as canonical rows.
        """
        mirna_terms = [term for term in (mirna_ids + list(mirna_aliases or []))
                       if term]
        gene_terms = [term for term in (gene_ids + list(gene_aliases or []))
                      if term]
        if not mirna_terms or not gene_terms:
            return []
        m = ",".join("?" for _ in mirna_terms)
        g = ",".join("?" for _ in gene_terms)
        return self._rows(
            "SELECT * FROM relations "
            f"WHERE LOWER(mirna_id) IN ({m}) AND LOWER(gene_id) IN ({g}) "
            "ORDER BY relation_id",
            [term.lower() for term in mirna_terms]
            + [term.lower() for term in gene_terms])

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._rows("SELECT key, value FROM meta")}

    def coverage(self) -> dict:
        counts = self._rows(
            "SELECT (SELECT COUNT(*) FROM documents) AS documents, "
            "(SELECT COUNT(*) FROM passages) AS passages, "
            "(SELECT COUNT(*) FROM relations) AS relations")
        meta = self.meta()
        return {
            **counts[0],
            "snapshots": sorted({v for k, v in meta.items() if k.startswith("snapshot:")}),
            # source:<name> is a count, so the name is in the key
            "sources": sorted(k.split(":", 1)[1] for k in meta if k.startswith("source:")),
            "search_mode": meta.get("search_mode", "fts5" if self.fts else "like"),
            "schema_version": int(meta.get("schema_version", "1")),
            "limited_corpus": True,
        }
