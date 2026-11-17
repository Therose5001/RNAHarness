#!/usr/bin/env python3
"""Local Occamy kernel — a drop-in `oh` replacement for the RNAHarness gateway.

The gateway's OpenHarnessOneShot spawns `oh -p <prompt> --output-format
stream-json` and reads NDJSON events from stdout. This script implements that
same event protocol against the locally running Occamy llama-server, with a
native OpenAI-style tool-call loop over the already-landed RNAHarness tools.

No external key, no egress: all inference stays on 127.0.0.1:8091 and all
tool calls are local. Never invents tool output.

Two reliability rules learned from a real multi-tool task (2026-11-14):
  * Exact sequences are passed by REFERENCE ID, registered from the original
    prompt by a deterministic regex. Asking an LLM to re-type a 50 nt window
    silently dropped a base. A near-match repair is kept as a second line of
    defence, and any repair is reported in the tool output.
  * A truncated generation (finish_reason == "length") is never reported as a
    successful answer. Generation continues a bounded number of times; if it
    is still truncated, an error event is emitted instead of `done`.

    python scripts/occamy_kernel.py -p "<prompt>" --output-format stream-json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("RNAHARNESS_ENTITY_DB", str(ROOT / "data" / "entities.sqlite"))
os.environ.setdefault("RNAHARNESS_EVIDENCE_DB", str(ROOT / "data" / "evidence.sqlite"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = os.environ.get("OCCAMY_BASE", "http://127.0.0.1:8091/v1")
MODEL = os.environ.get("OCCAMY_MODEL", "occamy-1.0")
MAX_TURNS = int(os.environ.get("OCCAMY_MAX_TURNS", "16"))
TIMEOUT = int(os.environ.get("OCCAMY_TIMEOUT_S", "120"))
MAX_TOKENS = int(os.environ.get("OCCAMY_MAX_TOKENS", "2048"))
MAX_CONTINUATIONS = int(os.environ.get("OCCAMY_MAX_CONTINUATIONS", "3"))

from rnaharness import kernel_catalog
from rnaharness.kernel_catalog import (EXTRA_SPEC, SYSTEM, CONTINUE_HINT)
from rnaharness.kernel_sequences import register_refs, ref_hint, _resolve, _repair_sequence
from rnaharness.kernel_execution import ToolRunCache, result_artifacts
from rnaharness.kernel_structure import structure_ref, run_structure

# Module-level aliases remain stable for callers that monkeypatch the kernel.
TOOLS_SPEC = kernel_catalog.TOOLS_SPEC
TOOL_META = kernel_catalog.TOOL_META

# --------------------------------------------------------------------------
# NDJSON event protocol
# --------------------------------------------------------------------------
def emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def chat(messages: list[dict], with_tools: bool = True) -> dict:
    payload = {"model": MODEL, "messages": messages,
               "max_tokens": MAX_TOKENS, "temperature": 0.0, "stream": False}
    if with_tools:
        payload["tools"] = kernel_catalog.TOOLS_SPEC
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def degenerate(text: str) -> bool:
    """Sentinel: reject the known '???' degeneration, empty or repeated junk."""
    t = (text or "").strip()
    if not t:
        return True
    if len(set(t)) <= 2 and len(t) > 8:
        return True
    return False


def _call_extra(name: str, args: dict) -> dict:
    """Dispatch the MCP-layer plain functions (no stdio needed)."""
    from rnaharness.servers import local_mcp as lm
    from rnaharness.servers import evidence_mcp as em
    from rnaharness.evidence import search as ev
    table = {
        "rna_identify": lambda: lm.rna_identify(str(args.get("sequence", ""))),
        "rna_fetch": lambda: lm.rna_fetch(str(args.get("accession", ""))),
        "mirna_resolve": lambda: lm.mirna_resolve(str(args.get("name", ""))),
        "gene_resolve": lambda: lm.gene_resolve(str(args.get("symbol", ""))),
        "id_map": lambda: lm.id_map(list(args.get("ids") or []),
                                    str(args.get("to_db", "")),
                                    str(args.get("from_db", ""))),
        "taxonomy_resolve": lambda: lm.taxonomy_resolve(str(args.get("name", ""))),
        "rfam_scan": lambda: lm.rfam_scan(str(args.get("sequence", "")),
                                          float(args.get("evalue", 1e-3)),
                                          bool(args.get("use_ga", True))),
        "evidence_lookup": lambda: em.tool_evidence_lookup(
            str(args.get("mirna", "")), str(args.get("gene", "")),
            str(args.get("species", "hsa"))),
        "citation_verify": lambda: em.tool_citation_verify(
            str(args.get("pmid_or_doc_id", "")), str(args.get("claim", "")),
            float(args.get("min_overlap", 0.8))),
        "resolve_pair": lambda: ev.resolve_pair(
            str(args.get("mirna", "")), str(args.get("gene", "")),
            str(args.get("species", "hsa"))),
    }
    if name not in table:
        return {"ok": False, "error": {"code": "unknown_tool", "message": name}}
    r = table[name]()
    # MCP functions already return the unified envelope; resolve_pair does not.
    if isinstance(r, dict) and "ok" in r:
        return r
    return {"ok": True, "data": r}


def _compact(r: dict) -> dict:
    """Keep tool output small enough for the model context, without losing truth."""
    if not isinstance(r, dict):
        return {"ok": False, "error": {"code": "bad_result"}}
    if r.get("ok") is False:
        return {"ok": False, "error": r.get("error")}
    d = r.get("data")
    if isinstance(d, dict):
        keep = {}
        for k in ("outcome", "evidence", "candidate_passages", "coverage", "note",
                  "structure", "mfe", "job_id", "score", "in_domain", "calibrated",
                  "window_nt", "sequence_nt", "n_ready", "n_total", "ready",
                  "degraded", "unreachable", "matches", "normalized", "result",
                  "status", "elapsed", "quote_match", "claim_support",
                  "resolved", "status_reason", "reason", "mirna_ids", "gene_ids",
                  "canonical_ids", "aliases", "xrefs", "lineage",
                  "scores", "files", "artifacts", "sequence_source", "prediction"):
            if k in d:
                keep[k] = d[k]
        if not keep:
            keep = {kk: vv for kk, vv in list(d.items())[:12]}
        return {"ok": True, "data": keep, "provenance": r.get("provenance")}
    return {"ok": True, "data": d}


def _progress_label(name: str, args: dict) -> str:
    """Short, human-readable description for the live progress stream."""
    show = []
    for k in ("mirna", "gene", "sequence_ref", "target_ref", "name", "symbol",
              "accession", "cancer", "preset", "tool", "sequences", "pdb",
              "pmid_or_doc_id", "ids", "to_db"):
        if k in args and args[k] not in (None, "", [], {}):
            v = args[k]
            if isinstance(v, list):
                v = "[%d 项]" % len(v)
            elif isinstance(v, str) and len(v) > 28:
                v = v[:25] + "…"
            show.append("%s=%s" % (k, v))
    return name + ("(" + ", ".join(show[:4]) + ")" if show else "")


def _result_brief(result: dict) -> str:
    """One-line summary of a tool result; never fabricates values."""
    if not isinstance(result, dict):
        return "无结果"
    if result.get("ok") is False:
        e = result.get("error") or {}
        return "错误 %s: %s" % (e.get("code", "?"), str(e.get("message", ""))[:80])
    d = result.get("data")
    if not isinstance(d, dict):
        return "完成"
    inner = d.get("result")
    if isinstance(inner, dict):
        d = inner
    for key in ("outcome", "structure", "mfe", "score", "job_id", "n_ready",
                "evidence", "candidate_passages", "matches", "resolved"):
        if key in d and d[key] is not None:
            v = d[key]
            if isinstance(v, (list, dict)):
                v = "%d 项" % len(v)
            return "%s=%s" % (key, str(v)[:60])
    cov = d.get("coverage")
    if isinstance(cov, dict) and cov.get("documents"):
        return "文档 %s / 段落 %s" % (cov.get("documents"), cov.get("passages"))
    return "完成"


def tool_call(name: str, args: dict, refs: dict[str, str] | None = None) -> dict:
    """Execute a real local tool. Dispatch is table-driven over TOOL_META."""
    refs = refs or {}
    from rnaharness.proto import adapter, labtools, scoring
    from rnaharness.evidence import search as evidence
    try:
        # ---- worker tools: OpenAPI-validated real job submission ----
        meta = kernel_catalog.TOOL_META.get(name) or {}
        if meta.get("kind") == "worker":
            worker = meta["worker"]
            if worker == "rhofold":
                seq, _ = _resolve(args, "sequence", "sequence_ref", refs)
                args = {k: v for k, v in args.items() if k != "sequence_ref"}
                args["sequence"] = seq
            r = adapter.run_tool(worker, args,
                                 timeout_s=float(os.environ.get(
                                     "OCCAMY_WORKER_TIMEOUT_S", "600")))
            return _compact(r)

        # ---- extra MCP-layer tools ----
        if name in {s["function"]["name"] for s in EXTRA_SPEC}:
            return _compact(_call_extra(name, args))

        # ---- high-level hand-written tools ----
        if name == "literature_evidence":
            mirna = str(args.get("mirna", "")).strip()
            gene = str(args.get("gene", "")).strip()
            lit = evidence.literature_search(mirna, gene,
                                             str(args.get("species", "hsa")))
            d = lit.get("data") or {}
            return {"ok": lit.get("ok", False), "outcome": d.get("outcome"),
                    "evidence": len(d.get("evidence") or []),
                    "candidate_passages": len(d.get("candidate_passages") or []),
                    "coverage": d.get("coverage"), "note": d.get("note")}
        if name == "rna_structure":
            seq, info = _resolve(args, "sequence", "sequence_ref", refs)
            r = adapter.run_tool("rhofold", {"sequence": seq}, timeout_s=float(
                os.environ.get("OCCAMY_WORKER_TIMEOUT_S", "600")))
            if r.get("ok"):
                r["data"].update(info, sequence_nt=len(seq), prediction=True)
            return _compact(r)
        if name == "rna_fold":
            seq, info = _resolve(args, "sequence", "sequence_ref", refs)
            r = labtools.vienna_fold(seq)
            if not r["ok"]:
                return {"ok": False, "error": r["error"], **info}
            x = r["data"]
            return {"ok": True, "structure": x["structure"], "mfe": x["mfe"],
                    "job_id": x["job_id"], "sequence_nt": len(seq), **info}
        if name == "lab_inventory":
            inv = labtools.inventory()
            return {"ok": True, "ready": inv["ready"],
                    "degraded": inv["degraded"],
                    "unreachable": inv["unreachable"],
                    "n_ready": len(inv["ready"]), "n_total": inv["n_total"]}
        if name == "seed_score":
            mir, i1 = _resolve(args, "mirna_sequence", "mirna_sequence_ref", refs)
            tgt, i2 = _resolve(args, "target", "target_ref", refs)
            r = scoring.seed_score(mir, tgt)
            return {"ok": True, **r, "target_nt": len(tgt), **i1, **i2}
        return {"ok": False, "error": {"code": "unknown_tool", "message": name}}
    except (KeyError, ValueError, TypeError) as exc:
        return {"ok": False, "error": {"code": "bad_reference",
                                       "message": str(exc)[:200]}}
    except Exception as exc:
        return {"ok": False,
                "error": {"code": type(exc).__name__, "message": str(exc)[:300]}}

# --------------------------------------------------------------------------
# continuation helpers
# --------------------------------------------------------------------------
def _trim_overlap(accumulated: str, nxt: str) -> str:
    """Drop the repeated prefix when a continuation restates the tail.

    Occamy often re-emits the last part of the previous chunk when asked to
    continue. Example: accumulated "abcde", continuation "defgh" -> "fgh".
    """
    for k in range(min(len(accumulated), len(nxt), 100), 2, -1):
        if accumulated[-k:] == nxt[:k]:
            return nxt[k:]
    return nxt


def _emit_deltas(text: str) -> None:
    for i in range(0, len(text), 24):
        emit({"type": "assistant_delta", "text": text[i:i + 24]})


_LEAD_IN = re.compile(
    r"^(?:All\b|Here is\b|The following\b|Below is\b|I've\b|I have\b)[^\n]*\n+",
    re.IGNORECASE)


def _enforce_chinese_start(text: str) -> str:
    """Occamy sometimes emits an English lead-in despite a Chinese-only system
    prompt. Drop only that first line; never alter the substantive content."""
    return _LEAD_IN.sub("", text, count=1).lstrip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", dest="prompt", required=True)
    ap.add_argument("--output-format", default="stream-json")
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    args = ap.parse_args(argv)
    if args.output_format != "stream-json":
        emit({"type": "error", "message": "only stream-json is supported",
              "recoverable": False})
        return 2

    refs = register_refs(args.prompt)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": args.prompt + ref_hint(refs)}]
    started = time.monotonic()
    cache = ToolRunCache(tool_call, refs)
    force_final = False
    try:
        direct_ref = structure_ref(args.prompt, refs)
        if direct_ref:
            return run_structure(direct_ref, refs, tool_call, emit)
        for _turn in range(args.max_turns):
            try:
                finalizing = force_final or _turn == args.max_turns - 1
                if finalizing:
                    messages.append({"role": "system", "content":
                        "停止调用工具。仅依据已有工具结果回答，明确说明已完成、失败或尚未完成的部分；"
                        "PDB仅可引用实际返回的artifact名称，不能把二级结构当成PDB或声称附件已送达。"})
                resp = chat(messages, with_tools=not finalizing)
            except urllib.error.HTTPError as exc:
                emit({"type": "error", "message": "Occamy HTTP %s: %s" % (
                    exc.code, exc.read(200).decode("utf-8", "replace")),
                    "recoverable": False})
                return 1
            except Exception as exc:
                emit({"type": "error", "message": "%s: %s" % (
                    type(exc).__name__, exc), "recoverable": False})
                return 1
            choices = resp.get("choices") or []
            if not choices:
                emit({"type": "error", "message": "Occamy returned no choices",
                      "recoverable": False})
                return 1
            choice = choices[0]
            msg = choice.get("message") or {}
            calls = msg.get("tool_calls") or []
            if calls:
                if finalizing:
                    emit({"type": "error", "message": "TOOL_LOOP_EXCEEDED: model ignored finalization",
                          "recoverable": False})
                    return 1
                messages.append({"role": "assistant",
                                 "content": msg.get("content") or "",
                                 "tool_calls": calls})
                for call in calls:
                    fn = call.get("function") or {}
                    name = str(fn.get("name") or "")
                    try:
                        targs = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        targs = {}
                    if not isinstance(targs, dict):
                        targs = {}
                    label = _progress_label(name, targs)
                    emit({"type": "status", "message": "调用工具：" + label})
                    emit({"type": "tool_started", "tool_name": name,
                          "tool_input": targs})
                    result, reused = cache.run(name, targs)
                    force_final = force_final or reused
                    ok = bool(result.get("ok"))
                    brief = _result_brief(result)
                    emit({"type": "status", "message":
                          ("完成：" if ok else "失败：") + label + " · " + brief})
                    emit({"type": "tool_completed", "tool_name": name,
                          "output": json.dumps(result, ensure_ascii=False)[:4000],
                          "is_error": not ok, "artifacts": result_artifacts(result)})
                    messages.append({"role": "tool",
                                     "tool_call_id": call.get("id") or name,
                                     "name": name,
                                     "content": json.dumps(result, ensure_ascii=False)})
                continue

            # final answer branch
            text = msg.get("content") or ""
            finish = str(choice.get("finish_reason") or "")
            if degenerate(text) and (text.strip() or finish != "length"):
                emit({"type": "error",
                      "message": "MODEL_DEGENERATED: Occamy output failed the degeneration sentinel",
                      "recoverable": False})
                return 1
            parts = [text]
            accumulated = text
            if text:
                _emit_deltas(text)
            continuations = 0
            while finish == "length" and continuations < MAX_CONTINUATIONS:
                continuations += 1
                emit({"type": "status", "message":
                      "输出达到长度上限，第 %d 次续写" % continuations})
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": CONTINUE_HINT})
                try:
                    resp2 = chat(messages, with_tools=False)
                except Exception as exc:
                    emit({"type": "error",
                          "message": "续写失败：%s: %s" % (type(exc).__name__, exc),
                          "recoverable": False})
                    return 1
                ch2 = (resp2.get("choices") or [{}])[0]
                m2 = ch2.get("message") or {}
                raw_next = m2.get("content") or ""
                if degenerate(raw_next):
                    emit({"type": "error", "message": "MODEL_DEGENERATED: invalid continuation",
                          "recoverable": False})
                    return 1
                finish = str(ch2.get("finish_reason") or "")
                nxt = _trim_overlap(accumulated, raw_next)
                if nxt:
                    parts.append(nxt)
                    accumulated += nxt
                    _emit_deltas(nxt)
                text = raw_next
            if finish == "length":
                emit({"type": "error",
                      "message": "输出在 %d 次续写后仍被截断，不返回不完整结果"
                                 % continuations, "recoverable": False})
                return 1
            final_text = _enforce_chinese_start("".join(parts).strip())
            if degenerate(final_text):
                emit({"type": "error",
                      "message": "Occamy output failed the degeneration sentinel",
                      "recoverable": False})
                return 1
            emit({"type": "assistant_complete", "text": final_text})
            emit({"type": "done", "text": final_text,
                  "continuations": continuations,
                  "elapsed_ms": int((time.monotonic() - started) * 1000)})
            return 0
        emit({"type": "error", "message": "tool loop exceeded max_turns",
              "recoverable": False})
        return 1
    except Exception as exc:
        emit({"type": "error", "message": "%s: %s" % (type(exc).__name__, exc),
              "recoverable": False})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
