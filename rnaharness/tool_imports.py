"""Approval, version activation, and execution of data-only imported tools."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from . import envelope as env
from .tool_import_source import GITHUB, PREFIX, build_snapshot, source_ref

TOOL = "imported_bioregistry_prefix"
REQUEST = "request_tool_import"
STATUS = "tool_import_status"
ADAPTER = "prefix-map/v1"
SETTING = "RNAHARNESS_TOOL_IMPORT_DB"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, state TEXT NOT NULL, manifest TEXT NOT NULL,
    digest TEXT NOT NULL, snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY, proposal TEXT NOT NULL REFERENCES proposals(id),
    kind TEXT NOT NULL, at INTEGER NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active (
    tool TEXT PRIMARY KEY, proposal TEXT NOT NULL REFERENCES proposals(id)
);
CREATE TABLE IF NOT EXISTS lookups (
    id TEXT PRIMARY KEY, proposal TEXT NOT NULL REFERENCES proposals(id),
    arguments TEXT NOT NULL, result TEXT NOT NULL, at INTEGER NOT NULL
);
"""
_FEATURES = {
    REQUEST: ("url", "Request an administrator-approved import or update from the Bioregistry paper or GitHub link."),
    STATUS: ("request_id", "Check the state of a tool import or update using the returned request ID."),
    TOOL: ("prefix", "Normalize a Bioregistry database prefix or alias to its canonical prefix."),
}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ToolImports:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def configured(cls) -> ToolImports | None:
        path = os.environ.get(SETTING)
        return cls(Path(path)) if path else None

    @contextmanager
    def _db(self, *, write: bool = False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(_SCHEMA)
            with db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def _active(db):
        return db.execute("SELECT proposal FROM active WHERE tool=?", (TOOL,)).fetchone()

    @staticmethod
    def _get(db, request_id: str):
        row = db.execute("SELECT * FROM proposals WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown tool import request ID.")
        return row

    @staticmethod
    def _event(db, request_id: str, kind: str, detail: str = "") -> None:
        db.execute("INSERT INTO events(proposal,kind,at,detail) VALUES (?,?,?,?)",
                   (request_id, kind, int(time.time()), detail))

    def submit(self, url: str) -> dict:
        ref = source_ref(url)
        commit, source_hash, mapping = build_snapshot(url)
        snapshot = _json(mapping)
        snapshot_hash = _digest(snapshot)
        with self._db(write=True) as db:
            active = self._active(db)
            baseline = active["proposal"] if active else None
            previous_commit = None
            if active:
                prior = self._get(db, active["proposal"])
                current = json.loads(prior["manifest"])
                previous_commit = current["commit"]
                if (current["snapshot_sha256"], current["commit"], current["adapter"]) == (snapshot_hash, commit, ADAPTER):
                    return self._view(db, prior)
            manifest = {
                "tool": TOOL, "adapter": ADAPTER, "commit": commit, "previous_request_id": baseline,
                "previous_commit": previous_commit,
                "source": f"{GITHUB}/commit/{commit}", "source_sha256": source_hash,
                "snapshot_sha256": snapshot_hash, "entries": len(mapping),
            }
            plan = _json(manifest)
            digest = _digest(plan)
            prior = db.execute("SELECT * FROM proposals WHERE digest=? AND state='pending'",
                               (digest,)).fetchone()
            if prior:
                return self._view(db, prior)
            request_id = uuid4().hex
            db.execute("INSERT INTO proposals VALUES (?, 'pending', ?, ?, ?)",
                       (request_id, plan, digest, snapshot))
            self._event(db, request_id, "import.requested", ref)
            return self._view(db, self._get(db, request_id))

    def status(self, request_id: str) -> dict:
        with self._db() as db:
            return self._view(db, self._get(db, request_id))

    def pending(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM proposals WHERE state='pending' ORDER BY rowid DESC")
            return [self._view(db, row) for row in rows]

    def _view(self, db, row) -> dict:
        events = db.execute("SELECT kind,at,detail FROM events WHERE proposal=? ORDER BY seq",
                            (row["id"],))
        return {"request_id": row["id"], "status": row["state"], "plan_sha256": row["digest"],
                "plan": json.loads(row["manifest"]), "events": [dict(event) for event in events]}

    def approve(self, request_id: str, digest: str) -> dict:
        """Operator-only entry point. Approval, verification and publishing are local."""
        with self._db(write=True) as db:
            row = self._get(db, request_id)
            if digest != row["digest"] or digest != _digest(row["manifest"]):
                raise ValueError("Approval must match the displayed plan SHA-256.")
            if row["state"] != "pending":
                return self._view(db, row)
            active = self._active(db)
            manifest = json.loads(row["manifest"])
            if manifest["previous_request_id"] != (active["proposal"] if active else None):
                raise ValueError("The active version has changed. Request a new review.")
            self._event(db, request_id, "import.approved", digest)
            try:
                self._verify(row, manifest)
            except ValueError as exc:
                db.execute("UPDATE proposals SET state='failed' WHERE id=?", (request_id,))
                self._event(db, request_id, "import.failed", str(exc))
            else:
                self._event(db, request_id, "import.verified")
                db.execute("INSERT INTO active VALUES (?,?) ON CONFLICT(tool) DO UPDATE SET proposal=excluded.proposal",
                           (TOOL, request_id))
                db.execute("UPDATE proposals SET state='published' WHERE id=?", (request_id,))
                self._event(db, request_id, "import.published", manifest["commit"])
            return self._view(db, self._get(db, request_id))

    @staticmethod
    def _verify(row, manifest: dict) -> None:
        if manifest["adapter"] != ADAPTER or _digest(row["snapshot"]) != manifest["snapshot_sha256"]:
            raise ValueError("Adapter version or snapshot digest differs from the approved plan.")
        mapping = json.loads(row["snapshot"])
        if (not isinstance(mapping, dict) or len(mapping) != manifest["entries"]
                or not all(PREFIX.fullmatch(k) and isinstance(v, str) and mapping.get(v) == v
                           for k, v in mapping.items())):
            raise ValueError("Snapshot contains invalid prefix mappings.")
        if any(mapping.get(prefix.casefold()) != expected
               for prefix, expected in (("CHEBI", "chebi"), ("GO", "go"), ("CHEBIID", "chebi"))):
            raise ValueError("Bioregistry reference lookups failed.")

    def is_active(self) -> bool:
        if not self.path.is_file():
            return False
        with self._db() as db:
            return self._active(db) is not None

    def lookup(self, prefix: str) -> dict:
        normalized = prefix.strip().casefold()
        if not PREFIX.fullmatch(normalized):
            raise ValueError("Supply a database prefix containing only letters, digits, dot, dash or underscore.")
        with self._db(write=True) as db:
            active = self._active(db)
            if active is None:
                raise ValueError("Bioregistry has no approved active version.")
            return self._lookup(db, active["proposal"], normalized)

    def _lookup(self, db, proposal: str, prefix: str, *, replay_of: str | None = None) -> dict:
        row = self._get(db, proposal)
        manifest = json.loads(row["manifest"])
        if manifest["adapter"] != ADAPTER or _digest(row["snapshot"]) != manifest["snapshot_sha256"]:
            raise ValueError("The recorded adapter or snapshot is unavailable.")
        canonical = json.loads(row["snapshot"]).get(prefix)
        run_id = uuid4().hex
        data = {"run_id": run_id, "prefix": prefix, "canonical_prefix": canonical,
                "resolved": canonical is not None}
        if replay_of:
            data["replay_of"] = replay_of
        provenance = env.provenance(TOOL, version=ADAPTER, snapshot=manifest["commit"],
                                    snapshot_sha256=manifest["snapshot_sha256"], request_id=proposal)
        result = env.ok(data, provenance)
        db.execute("INSERT INTO lookups VALUES (?,?,?,?,?)",
                   (run_id, proposal, _json({"prefix": prefix}), _json(result), int(time.time())))
        return result

    def replay(self, run_id: str) -> dict:
        with self._db(write=True) as db:
            row = db.execute("SELECT * FROM lookups WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown lookup run ID.")
            result = self._lookup(db, row["proposal"], json.loads(row["arguments"])["prefix"], replay_of=run_id)
            result["comparison"] = {"matches": result["data"]["canonical_prefix"] ==
                                    json.loads(row["result"])["data"]["canonical_prefix"]}
            return result


def tool_specs() -> list[dict]:
    store = ToolImports.configured()
    if store is None:
        return []
    names = (REQUEST, STATUS, TOOL) if store.is_active() else (REQUEST, STATUS)
    specs = []
    for name in names:
        key, description = _FEATURES[name]
        specs.append({"name": name, "description": description, "parameters": {
            "type": "object", "properties": {key: {"type": "string"}},
            "required": [key], "additionalProperties": False,
        }})
    return specs


def is_import_tool(name: str) -> bool:
    return name in _FEATURES


def call_import_tool(name: str, args: dict) -> dict:
    provenance = env.provenance("tool_imports")
    store = ToolImports.configured()
    if store is None:
        return env.err(env.BACKEND_UNAVAILABLE, "Tool imports are not configured.", provenance)
    key = _FEATURES[name][0]
    if set(args) != {key} or not isinstance(args[key], str) or len(args[key]) > 512:
        return env.err(env.INVALID_PARAMS, f"Expected one string argument: {key}.", provenance)
    try:
        if name == REQUEST:
            return env.ok(store.submit(args[key]), provenance)
        if name == STATUS:
            return env.ok(store.status(args[key]), provenance)
        return store.lookup(args[key])
    except ValueError as exc:
        return env.err(env.INVALID_PARAMS, str(exc), provenance)


def request_link_answer(prompt: str) -> str | None:
    """Handle a bare supported link in the current gateway message or direct prompt."""
    if not os.environ.get(SETTING):
        return None
    url = prompt.strip()
    close_tag = "\n</current_message>"
    if url.endswith(close_tag):
        url = url[:-len(close_tag)].rsplit("<current_message>\n", 1)[-1].strip()
    if url.startswith("当前用户消息：\n"):
        url = url.partition("\n")[2].strip()
    try:
        source_ref(url)
    except ValueError:
        return None
    result = call_import_tool(REQUEST, {"url": url})
    if not result["ok"]:
        return f"工具接入申请失败。{result['error']['message']}"
    data = result["data"]
    status = "该版本已发布" if data["status"] == "published" else "已提交工具接入申请，等待管理员审批"
    return f"{status}。申请 ID `{data['request_id']}`，版本 `{data['plan']['commit'][:12]}`。"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review and approve RNAHarness tool imports locally.")
    parser.add_argument("--db", type=Path, default=os.environ.get(SETTING))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending")
    sub.add_parser("review")
    request = sub.add_parser("request")
    request.add_argument("url")
    status = sub.add_parser("status")
    status.add_argument("request_id")
    approve = sub.add_parser("approve")
    approve.add_argument("request_id")
    approve.add_argument("plan_sha256")
    replay = sub.add_parser("replay")
    replay.add_argument("run_id")
    args = parser.parse_args(argv)
    if args.db is None:
        parser.error(f"Set {SETTING} or supply --db.")
    store = ToolImports(args.db)
    if args.command == "review":
        from .tool_import_review import review

        review(store)
        return 0
    try:
        if args.command == "pending":
            result = store.pending()
        elif args.command == "request":
            result = store.submit(args.url)
        elif args.command == "status":
            result = store.status(args.request_id)
        elif args.command == "approve":
            result = store.approve(args.request_id, args.plan_sha256)
        else:
            result = store.replay(args.run_id)
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 1 if isinstance(result, dict) and result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
