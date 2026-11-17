"""Offline prototype tests: no key, no network, no GPU."""
from __future__ import annotations

import pytest

from rnaharness import envelope as env
from rnaharness.proto import PROTO_VERSION, scan_windows, seed_score
from rnaharness.proto.answer import answer_pair
from rnaharness.proto.selftest import run as selftest

MIRNA = "UAGCUUAUCAGACUGAUGUUGA"  # hsa-miR-21-5p
TARGET50 = "A" * 50


def test_seed_score_is_uncalibrated_count():
    r = seed_score(MIRNA, TARGET50)
    assert r["calibrated"] is False
    assert r["score_kind"] == "uncalibrated_seed_match_count"
    assert r["in_domain"] is True
    assert r["score"] == 0.0


def test_seed_score_matches_published_baseline():
    # seed7 of MIRNA[1:8] reverse-complement must be present in the target
    from rnaharness.proto.scoring import reverse_complement_rna
    rc_seed = reverse_complement_rna(MIRNA[1:8])
    hit = seed_score(MIRNA, rc_seed + "A" * 43)
    assert hit["score"] >= 2.0


def test_ood_input_rejected():
    with pytest.raises(ValueError):
        seed_score(MIRNA, "ACGT" * 400)
    with pytest.raises(ValueError):
        seed_score("ACGU", "not-rna!!!")


def test_scan_windows_covers_tail():
    r = scan_windows(MIRNA, "A" * 120, window=50, step=10)
    assert r["n_windows"] >= 8
    assert r["per_window_scores"][-1]["offset"] == 70
    assert r["aggregation"] == "max"
    assert r["calibrated"] is False


def test_answer_is_three_state_and_evidence_first():
    r = answer_pair(MIRNA, "PTEN", TARGET50, mirna_name="miR-21")
    assert r["ok"] is True
    assert set(r.keys()) == {"ok", "data", "error", "provenance", "evidence"}
    assert r["provenance"]["version"] == PROTO_VERSION
    assert r["provenance"]["fusion"] == "evidence_first"
    assert r["data"]["no_rnaqwen"] is True
    assert r["data"]["state"] in ("experimental_evidence", "prediction_only",
                                  "scoring_unavailable", "not_retrieved")
    assert r["data"]["prediction"]["is_prediction"] is True
    assert r["data"]["prediction"]["calibrated"] is False
    assert r["data"]["prediction"]["result"] is not None
    assert r["data"]["query"]["mirna_name"] == "miR-21"


def test_name_as_sequence_does_not_fabricate_a_score():
    r = answer_pair("miR-21", "PTEN", TARGET50)
    assert r["ok"] is True
    assert r["data"]["prediction"]["result"] is None
    assert r["data"]["prediction"]["error"]
    assert "0.0" not in r["data"]["answer_text"]
    assert "打分未完成" in r["data"]["answer_text"]
    assert r["data"]["state"] == "scoring_unavailable"


def test_unresolved_pair_is_not_reported_as_absence():
    r = answer_pair(MIRNA, "NOTAGENE", mirna_name="miR-999999")
    assert r["ok"] is True
    text = r["data"]["answer_text"]
    assert "不存在" not in text or "不表示" in text
    assert r["data"]["literature"]["limited_corpus"] is True


def test_prediction_never_enters_evidence_list():
    r = answer_pair(MIRNA, "PTEN", TARGET50, mirna_name="miR-21")
    assert all(e["kind"] == "tool_output" for e in r["evidence"])


def test_invalid_query_uses_stable_error_code():
    r = answer_pair("", "PTEN")
    assert r["ok"] is False
    assert r["error"]["code"] == env.INVALID_PARAMS


def test_selftest_is_green():
    report = selftest()
    assert report["ok"] is True, report
