"""Initialize a reproducible starter kit for the researcher MCP server."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

from rnaharness.entity import EntityStore
from rnaharness.entity.loaders import load_hgnc, load_mirbase
from rnaharness.evidence.store import EvidenceStore

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "rnaharness-research" / "SKILL.md"
FIXTURES = ROOT / "tests" / "fixtures"
TOOL_NAMES = ["normalize_prefix", "resolve_human_gene", "resolve_human_mirna",
              "indexed_citation", "fold_reference_hairpin"]


def _write_once(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except FileExistsError:
        pass


def _entities(db: Path) -> None:
    store = EntityStore.open(db, create=True)
    try:
        load_hgnc(store, FIXTURES / "hgnc_mini.tsv", snapshot="repository-demo")
        load_mirbase(store, FIXTURES / "mature_mini.fa", snapshot="repository-demo")
        store.commit()
    finally:
        store.close()


def _citations(db: Path) -> None:
    store = EvidenceStore.open(db, create=True)
    try:
        store.upsert_document({
            "doc_id": "PMC1855228", "pmid": "17437991", "pmcid": "PMC1855228",
            "doi": "10.1101/gad.1540407", "title": "The tumor suppressor microRNA let-7 represses the HMGA2 oncogene",
            "year": 2007, "source": "pmc", "snapshot": "repository-demo",
        })
        store.set_meta("snapshot:pmc", "repository-demo")
        store.set_meta("source:pmc", "1")
        store.commit()
    finally:
        store.close()


def initialize(destination: Path) -> dict:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    entities = destination / "entities.sqlite"
    citations = destination / "evidence.sqlite"
    for db, build in ((entities, _entities), (citations, _citations)):
        if not db.is_file():
            with TemporaryDirectory(dir=destination) as stage:
                staged_db = Path(stage) / db.name
                build(staged_db)
                try:
                    os.link(staged_db, db)
                except FileExistsError:
                    pass

    env = {
        "RNAHARNESS_ENTITY_DB": str(entities),
        "RNAHARNESS_EVIDENCE_DB": str(citations),
        "RNAHARNESS_TOOL_IMPORT_DB": str(destination / "registry.sqlite"),
        "RNAHARNESS_REFERENCE_FOLD_BACKEND": "local",
        "RNAHARNESS_RESEARCH_RUNS": str(destination / "jobs"),
        "PYTHONIOENCODING": "utf-8",
    }
    mcp = {
        "command": sys.executable,
        "args": ["-m", "rnaharness.servers.research_mcp"],
        "cwd": str(ROOT),
        "env": env,
        "toolFilter": {"include": TOOL_NAMES},
    }
    workspace = destination / "workspace"
    _write_once(workspace / "skills" / "rnaharness-research" / "SKILL.md",
                SKILL.read_text(encoding="utf-8"))
    config = {
        "gateway": {"mode": "local", "bind": "loopback"},
        "agents": {"defaults": {"workspace": str(workspace)}},
        "session": {"dmScope": "per-account-channel-peer"},
        "mcp": {"servers": {"rnaharness-research": mcp}},
        "tools": {"allow": ["bundle-mcp"], "deny": [
            "group:fs", "group:runtime", "group:automation", "group:web", "message", "gateway",
        ]},
        "commands": {"native": False, "nativeSkills": False},
    }
    config_path = destination / "openclaw.json"
    _write_once(config_path, json.dumps(config, indent=2) + "\n")
    return {"config": str(config_path), "state": str(destination / "openclaw-state"),
            "registry": env["RNAHARNESS_TOOL_IMPORT_DB"], "workspace": str(workspace)}


def enable_channels(config_path: Path, wechat: bool, qq_users: list[str]) -> None:
    if any(not re.fullmatch(r"[A-Z0-9_-]{1,128}", item) for item in qq_users):
        raise ValueError("Each QQ recipient must be one explicit uppercase user OpenID.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    channels = config.setdefault("channels", {})
    if wechat:
        channels.setdefault("openclaw-weixin", {})["enabled"] = True
    if qq_users:
        channels.setdefault("qqbot", {}).update({
            "enabled": True, "dmPolicy": "allowlist", "groupPolicy": "disabled",
            "allowFrom": list(dict.fromkeys(qq_users)),
        })
    with TemporaryDirectory(dir=config_path.parent) as stage:
        draft = Path(stage) / config_path.name
        draft.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        draft.replace(config_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runs" / "research-stack")
    parser.add_argument("--enable-wechat", action="store_true")
    parser.add_argument("--qq-openid", default=[], action="append")
    args = parser.parse_args()
    paths = initialize(args.output)
    if args.enable_wechat or args.qq_openid:
        try:
            enable_channels(Path(paths["config"]), args.enable_wechat, args.qq_openid)
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(paths, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
