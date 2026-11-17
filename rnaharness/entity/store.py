"""SQLite entity store.

Four tables, on purpose (PLAN.md §6.2): an entity, its names, its cross
references, and load metadata. No embeddings here — this is identity, not
evidence.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .naming import alias_key

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "entities.sqlite"
DB_ENV = "RNAHARNESS_ENTITY_DB"

KINDS = ("rna", "mirna", "gene", "taxon", "family")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,
  canonical_id TEXT NOT NULL,
  name         TEXT,
  species      TEXT,
  taxid        TEXT,
  parent       TEXT,
  sequence     TEXT,
  seq_hash     TEXT,
  source       TEXT,
  snapshot     TEXT,
  UNIQUE (kind, canonical_id)
);
CREATE INDEX IF NOT EXISTS entities_seq ON entities (seq_hash);
CREATE INDEX IF NOT EXISTS entities_name ON entities (kind, name);
CREATE INDEX IF NOT EXISTS entities_canon ON entities (canonical_id);

CREATE TABLE IF NOT EXISTS aliases (
  entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
  alias     TEXT NOT NULL,
  norm      TEXT NOT NULL,
  source    TEXT,
  PRIMARY KEY (entity_id, alias, source)
);
CREATE INDEX IF NOT EXISTS aliases_norm ON aliases (norm);

CREATE TABLE IF NOT EXISTS xrefs (
  entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
  db        TEXT NOT NULL,
  accession TEXT NOT NULL,
  PRIMARY KEY (entity_id, db, accession)
);
CREATE INDEX IF NOT EXISTS xrefs_acc ON xrefs (db, accession);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

SCHEMA_VERSION = 1


class NotLoaded(RuntimeError):
    """The entity database or a requested source is missing."""

    def __init__(self, path: Path, detail: str = ""):
        self.path = path
        self.detail = detail
        super().__init__(f"entity database not loaded at {path}")

    def coverage(self) -> dict:
        return {
            "path": str(self.path),
            "loaded_sources": [],
            "detail": self.detail or "build it with scripts/entity_build.py",
        }


def default_db_path() -> Path:
    return Path(os.environ.get(DB_ENV) or DEFAULT_DB)


class EntityStore:
    def __init__(self, path: Path | str, conn: sqlite3.Connection,
                 read_only: bool = False):
        self.path = Path(path)
        self.conn = conn
        self.read_only = read_only

    # -- lifecycle -----------------------------------------------------
    @classmethod
    def open(cls, path: Path | str | None = None, create: bool = False) -> "EntityStore":
        target = Path(path) if path else default_db_path()
        exists = target.exists()
        empty = exists and target.stat().st_size == 0
        if (not exists or empty) and not create:
            raise NotLoaded(target)
        if create:
            target.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(target))
        else:
            conn = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if create:
            conn.execute("PRAGMA foreign_keys = ON")
            # Existing databases are never altered implicitly. A new or empty
            # file is the only case where this method creates tables.
            if not exists or empty:
                conn.executescript(SCHEMA)
                cls(target, conn, read_only=False).set_meta(
                    "schema_version", str(SCHEMA_VERSION))
                conn.commit()
            return cls(target, conn, read_only=False)
        conn.execute("PRAGMA query_only = ON")
        return cls(target, conn, read_only=True)

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # -- writes --------------------------------------------------------
    def upsert_entity(
        self,
        kind: str,
        canonical_id: str,
        *,
        name: str | None = None,
        species: str | None = None,
        taxid: str | None = None,
        parent: str | None = None,
        sequence: str | None = None,
        seq_hash: str | None = None,
        source: str | None = None,
        snapshot: str | None = None,
    ) -> int:
        row = self.conn.execute(
            """
            INSERT INTO entities
              (kind, canonical_id, name, species, taxid, parent, sequence,
               seq_hash, source, snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (kind, canonical_id) DO UPDATE SET
              name     = COALESCE(excluded.name, entities.name),
              species  = COALESCE(excluded.species, entities.species),
              taxid    = COALESCE(excluded.taxid, entities.taxid),
              parent   = COALESCE(excluded.parent, entities.parent),
              sequence = COALESCE(excluded.sequence, entities.sequence),
              seq_hash = COALESCE(excluded.seq_hash, entities.seq_hash),
              source   = COALESCE(excluded.source, entities.source),
              snapshot = COALESCE(excluded.snapshot, entities.snapshot)
            RETURNING id
            """,
            (kind, canonical_id, name, species, taxid, parent, sequence,
             seq_hash, source, snapshot),
        ).fetchone()
        return int(row["id"])

    def add_alias(self, entity_id: int, alias: str, source: str = "") -> None:
        if not alias:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO aliases (entity_id, alias, norm, source) "
            "VALUES (?, ?, ?, ?)",
            (entity_id, alias, alias_key(alias), source),
        )

    def add_xref(self, entity_id: int, db: str, accession: str) -> None:
        if not accession:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO xrefs (entity_id, db, accession) VALUES (?, ?, ?)",
            (entity_id, db, str(accession)),
        )

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    # -- reads ---------------------------------------------------------
    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def get(self, kind: str, canonical_id: str) -> dict | None:
        rows = self._rows(
            "SELECT * FROM entities WHERE kind = ? AND canonical_id = ?",
            (kind, canonical_id),
        )
        return rows[0] if rows else None

    def by_sequence(self, seq_hash: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM entities WHERE seq_hash = ? ORDER BY kind, canonical_id",
            (seq_hash,),
        )

    def by_accession(self, accession: str) -> list[dict]:
        return self._rows(
            "SELECT e.* FROM entities e "
            "WHERE e.canonical_id = ? "
            "UNION "
            "SELECT e.* FROM entities e JOIN xrefs x ON x.entity_id = e.id "
            "WHERE x.accession = ?",
            (accession, accession),
        )

    def by_alias(self, keys: list[str], kind: str | None = None) -> list[dict]:
        if not keys:
            return []
        marks = ",".join("?" for _ in keys)
        sql = (
            "SELECT DISTINCT e.* FROM entities e "
            "LEFT JOIN aliases a ON a.entity_id = e.id "
            f"WHERE (a.norm IN ({marks}) OR LOWER(e.canonical_id) IN ({marks}) "
            f"OR LOWER(e.name) IN ({marks}))"
        )
        args: list[Any] = keys * 3
        if kind:
            sql += " AND e.kind = ?"
            args.append(kind)
        return self._rows(sql, args)

    def aliases_for(self, entity_id: int) -> list[dict]:
        return self._rows(
            "SELECT alias, source FROM aliases WHERE entity_id = ? ORDER BY alias",
            (entity_id,),
        )

    def xrefs_for(self, entity_id: int) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for row in self._rows(
            "SELECT db, accession FROM xrefs WHERE entity_id = ? ORDER BY db, accession",
            (entity_id,),
        ):
            out.setdefault(row["db"], []).append(row["accession"])
        return out

    def map_ids(self, ids: list[str], to_db: str, from_db: str | None = None) -> list[dict]:
        out = []
        for item in ids:
            if from_db:
                rows = self._rows(
                    "SELECT e.*, x.accession AS _from FROM entities e "
                    "JOIN xrefs x ON x.entity_id = e.id "
                    "WHERE x.db = ? AND x.accession = ?",
                    (from_db, item),
                )
            else:
                rows = self.by_accession(item)
            for row in rows:
                xrefs = self.xrefs_for(row["id"])
                out.append({
                    "input": item,
                    "kind": row["kind"],
                    "canonical_id": row["canonical_id"],
                    "name": row["name"],
                    "mapped": xrefs.get(to_db, []),
                    "xrefs": xrefs,
                })
        return out

    def taxonomy_lineage(self, taxid: str) -> list[dict]:
        lineage = []
        seen: set[str] = set()
        current = taxid
        while current and current not in seen:
            seen.add(current)
            row = self.get("taxon", current)
            if not row:
                break
            lineage.append({"taxid": current, "name": row["name"]})
            current = row.get("parent") or ""
        return lineage

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._rows("SELECT key, value FROM meta")}

    def info(self) -> dict:
        counts = {
            r["kind"]: r["n"]
            for r in self._rows("SELECT kind, COUNT(*) AS n FROM entities GROUP BY kind")
        }
        return {
            "path": str(self.path),
            "counts": counts,
            "meta": self.meta(),
        }
