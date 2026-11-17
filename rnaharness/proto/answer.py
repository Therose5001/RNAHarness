"""Evidence-first fusion + answer contract (PLAN.md §1.5, §2.2).

Three states are strictly separated:
  * experimental evidence  -> indexed structured relation claims
  * model prediction       -> explicitly labelled, heuristic stand-in
  * not retrieved          -> coverage + snapshot, never "does not exist"
"""
from __future__ import annotations

import time
from typing import Any

from .. import envelope as env
from ..evidence import search as evidence
from . import PROTO_VERSION, scan_windows
from . import labtools

SOURCE = "rnaharness.proto"
LIMITED = "Results cover only the indexed corpus and snapshot; this limited corpus is not evidence of absence."


def _prov(started: float, **extra: Any) -> dict:
    return env.provenance(SOURCE, version=PROTO_VERSION, started_at=started,
                          **extra)


def _literature(mirna: str, gene: str, species: str, top_k: int) -> dict:
    """FTS5 retrieval via the existing evidence layer; failures are data, not crashes."""
    try:
        result = evidence.literature_search(mirna, gene, species, top_k)
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}",
                "outcome": "retrieval_unavailable", "candidate_passages": [],
                "evidence": [], "coverage": {}, "resolution": {}}
    data = result.get("data") or {}
    return {
        "available": result.get("ok", False),
        "error": (result.get("error") or {}).get("message"),
        "outcome": data.get("outcome"),
        "candidate_passages": data.get("candidate_passages") or [],
        "evidence": data.get("evidence") or [],
        "coverage": data.get("coverage") or {},
        "resolution": data.get("resolution") or {},
        "limited_corpus": data.get("limited_corpus", True),
        "note": data.get("note") or LIMITED,
    }


def _answer_text(state: str, mirna_name: str, gene: str, lit: dict,
                 score: dict, score_error: str | None) -> str:
    cov = lit.get("coverage") or {}
    coverage = (f"本库快照 {','.join(cov.get('snapshots') or []) or '未知'}，"
                f"收录 {cov.get('documents', '?')} 篇文献 / "
                f"{cov.get('passages', '?')} 个段落")
    if state == "experimental_evidence":
        n = len(lit["evidence"])
        return (f"【有实验证据】在收录文献中检索到 {n} 条结构化关系断言"
                f"（{mirna_name} — {gene}）；{coverage}。"
                f"注意：这是索引中的断言记录，不等于科学事实已被本系统复核。")
    if state == "prediction_only":
        s = score.get("best_score", score.get("score"))
        return (f"【只有模型预测】启发式打分 {s}（seed-match 计数，未校准，"
                f"非概率）；**未在检索到的文献中找到实验证据**。"
                f"这是 RNAQwen 家族尚未接入时的占位结果，不能作为生物学结论；"
                f"{coverage}。")
    if state == "scoring_unavailable":
        return (f"【打分未完成】{score_error}。"
                f"**未在检索到的文献中找到实验证据**；{coverage}。"
                f"本系统不给出未经计算的分数，也不把未打分说成预测。")
    return (f"【未检索到】在收录的 {cov.get('documents', '?')} 篇miRNA-mRNA相关文献中"
            f"未检索到该关系（覆盖范围：{coverage}）。"
            f"这不表示该关系不存在。")


def answer_pair(mirna: str, gene: str, target: str = "", species: str = "hsa",
                window: int = 50, step: int = 10, top_k: int = 10,
                wobble: bool = False, mirna_name: str | None = None,
                with_fold: bool = False, with_labs: bool = False) -> dict:
    """Full offline loop: retrieve -> score -> fuse -> render.

    `mirna` is the mature SEQUENCE used for scoring. `mirna_name` is only used
    for literature retrieval; when omitted the sequence itself is used, which
    will not match FTS5 text. Never fabricate a score when scoring fails.
    """
    from . import scoring

    started = time.monotonic()
    if not mirna.strip() or not gene.strip():
        return env.err(env.INVALID_PARAMS, "mirna and gene are required",
                       _prov(started))
    name = (mirna_name or mirna).strip()
    lit = _literature(name, gene.strip(), species, top_k)
    labs = labtools.inventory() if with_labs else None
    fold = labtools.vienna_fold(target) if (with_fold and target.strip()) else None
    score: dict = {}
    score_error: str | None = None
    if target:
        try:
            score = (scan_windows(mirna, target, window=window, step=step,
                                  wobble=wobble) if len(target.strip()) > window
                     else scoring.seed_score(mirna, target, wobble))
        except (ValueError, OSError) as exc:
            score_error = str(exc)
    else:
        score_error = "no target sequence supplied; scoring skipped"

    # Evidence-first (PLAN §2.2): a structured claim wins; prediction is
    # reported separately and NEVER merged into the evidence list.
    if lit["evidence"]:
        state = "experimental_evidence"
    elif score:
        state = "prediction_only"
    elif score_error:
        state = "scoring_unavailable"
    else:
        state = "not_retrieved"

    data = {
        "query": {"mirna": mirna, "mirna_name": name, "gene": gene,
                  "species": species,
                  "target_nt": len(target.strip()) if target else 0},
        "state": state,
        "answer_text": _answer_text(state, name, gene, lit, score, score_error),
        "literature": lit,
        "computable": {"lab_inventory": labs, "vienna_fold": fold},
        "prediction": {"model": "simple-algorithm-standin",
                       "calibrated": False, "is_prediction": True,
                       "replaces": "rna_relation / rna_scan (RNAQwen family)",
                       "result": score or None, "error": score_error},
        "no_rnaqwen": True,
        "disclaimer": ("Prototype without the RNAQwen family. Scores are "
                       "uncalibrated seed-match counts from the published "
                       "baseline, not model probabilities."),
    }
    ev = [{"kind": "tool_output", "tool": "literature_search",
           "outcome": lit["outcome"]}]
    if fold and fold.get("ok"):
        ev.append({"kind": "tool_output", "tool": "vienna.fold",
                   "job_id": fold["data"]["job_id"],
                   "result_hash_source": fold["provenance"]})
    return env.ok(data, _prov(started, state=state,
                              retrieval_outcome=lit["outcome"],
                              fusion="evidence_first",
                              literature_available=lit["available"],
                              lab_ready=(labs or {}).get("ready", []),
                              fold_ok=bool(fold and fold.get("ok"))), ev)
