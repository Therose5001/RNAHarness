"""End-to-end offline self-check. No API key, no network, no GPU."""
from __future__ import annotations

from pathlib import Path

from . import PROTO_VERSION, scan_windows, seed_score
from .answer import answer_pair

ROOT = Path(__file__).resolve().parents[2]


def run() -> dict:
    checks: list[dict] = []

    def check(name: str, fn) -> None:
        try:
            value = fn()
            checks.append({"name": name, "ok": True, "detail": value})
        except Exception as exc:
            checks.append({"name": name, "ok": False,
                           "detail": f"{type(exc).__name__}: {exc}"})

    check("databases present", lambda: {
        "entities": (ROOT / "data" / "entities.sqlite").exists(),
        "evidence": (ROOT / "data" / "evidence.sqlite").exists()})
    check("seed_score on 50nt window", lambda: seed_score(
        "UAGCUUAUCAGACUGAUGUUGA", "A" * 50)["score_kind"])
    check("scan_windows sliding", lambda: scan_windows(
        "UAGCUUAUCAGACUGAUGUUGA", "A" * 120)["n_windows"])
    def ood_rejects():
        try:
            seed_score("ACGU", "not-rna!!!")
        except ValueError:
            return "ValueError raised as expected"
        raise AssertionError("invalid RNA characters were not rejected")
    check("OOD rejection", ood_rejects)

    MIR = "UAGCUUAUCAGACUGAUGUUGA"

    def full():
        r = answer_pair(MIR, "PTEN", "A" * 50, mirna_name="miR-21")
        assert r["ok"], r["error"]
        assert r["provenance"]["source"] == "rnaharness.proto"
        assert r["data"]["no_rnaqwen"] is True
        assert r["data"]["state"] in ("experimental_evidence",
                                      "prediction_only", "scoring_unavailable",
                                      "not_retrieved")
        assert r["data"]["prediction"]["result"] is not None
        return {"state": r["data"]["state"],
                "retrieval_outcome": r["data"]["literature"]["outcome"],
                "n_candidates": len(r["data"]["literature"]["candidate_passages"]),
                "score": r["data"]["prediction"]["result"]["score"]}
    check("full answer loop", full)

    def no_fabrication():
        r = answer_pair("miR-21", "PTEN", "A" * 50)
        assert r["data"]["prediction"]["result"] is None
        assert r["data"]["prediction"]["error"]
        assert "0.0" not in r["data"]["answer_text"]
        assert "打分未完成" in r["data"]["answer_text"]
        assert r["data"]["state"] == "scoring_unavailable"
        return "name-as-sequence is rejected without a fabricated score"
    check("no fabricated score on invalid sequence", no_fabrication)

    def tri_state():
        r = answer_pair(MIR, "NOTAGENE", mirna_name="miR-999999")
        return {"ok": r["ok"], "state": r["data"]["state"],
                "answer_text": r["data"]["answer_text"][:80]}
    check("unresolved pair is not absence", tri_state)

    return {"ok": all(c["ok"] for c in checks),
            "version": PROTO_VERSION,
            "checks": checks,
            "note": "All checks must pass; OOD rejection passes when a "
                    "ValueError is raised."}
