"""Generic adapter tests: schema discovery, validation, and one real job."""
from __future__ import annotations

import pytest
import json

from rnaharness import envelope as env
from rnaharness.proto import adapter, labtools


def test_unknown_tool_is_not_found():
    assert adapter.contract("nope")["error"]["code"] == env.NOT_FOUND
    assert adapter.capabilities("nope")["error"]["code"] == env.NOT_FOUND


def test_contract_is_discovered_from_openapi():
    h = labtools.probe_one("vienna")
    if not (h["reachable"] and h["ok"] and h["status"] == "ready"):
        pytest.skip("ViennaRNA worker not ready")
    c = adapter.contract("vienna")
    assert c["ok"] is True, c["error"]
    assert c["data"]["port"] == 8015
    assert c["data"]["run_path"] == "/api/run"
    assert c["provenance"]["discovery"] == "openapi.json"
    assert "/api/run" in c["data"]["post_paths"]
    assert "tool" in c["data"]["fields"]


def test_schema_validation_rejects_bad_payload():
    h = labtools.probe_one("vienna")
    if not (h["reachable"] and h["ok"] and h["status"] == "ready"):
        pytest.skip("ViennaRNA worker not ready")
    # ViennaRNA's schema has no required fields, so use a tool that does.
    c = adapter.contract("gatsite")
    if not c["ok"]:
        pytest.skip("GATRsite contract unavailable")
    r = adapter.validate_payload("gatsite", {})
    assert r["ok"] is False
    assert any("pdb" in e for e in r["errors"])


def test_dry_run_never_submits():
    h = labtools.probe_one("vienna")
    if not (h["reachable"] and h["ok"] and h["status"] == "ready"):
        pytest.skip("ViennaRNA worker not ready")
    r = adapter.run_tool("vienna", {"tool": "fold",
                                    "params": {"sequence": "GGGAAACCC"}},
                         dry_run=True)
    assert r["ok"] is True
    assert r["data"]["dry_run"] is True
    assert r["provenance"]["submitted"] is False


@pytest.mark.live
def test_real_vienna_fold_through_generic_adapter():
    h = labtools.probe_one("vienna")
    if not (h["reachable"] and h["ok"] and h["status"] == "ready"):
        pytest.skip("ViennaRNA worker not ready")
    r = adapter.run_tool("vienna", {"tool": "fold",
                                    "params": {"sequence": "GGGAAACCC"}},
                         timeout_s=30)
    assert r["ok"] is True, r["error"]
    assert r["data"]["result"]["structure"] == "(((...)))"
    assert r["data"]["result"]["mfe"] == pytest.approx(-1.2, abs=1e-4)
    assert r["data"]["job_id"]


def predict_spec():
    return {'info': {'title': 'RhoFold'}, 'paths': {
        '/api/predict': {'post': {'requestBody': {'content': {'application/json': {
            'schema': {'type': 'object', 'properties': {'sequence': {'type': 'string'}},
                       'required': ['sequence']}}}}}}, '/api/stop': {'post': {}}}}


def test_predict_contract_submission_and_real_artifacts(monkeypatch):
    posted = []
    def get(port, path, timeout=8):
        if path == '/openapi.json':
            return 200, json.dumps(predict_spec())
        assert path == '/api/result/job-1'
        return 200, json.dumps({'status': 'done', 'scores': {'length': 13},
                               'files': ['model.pdb', '../secret.pdb', 'scores.json']})
    def post(port, path, payload):
        posted.append((port, path, payload))
        return 200, '{"job_id":"job-1"}'
    monkeypatch.setattr(adapter, '_get', get)
    monkeypatch.setattr(adapter, '_post', post)
    dry = adapter.run_tool('rhofold', {'sequence': 'AUAUUAUAUAUAU'}, dry_run=True)
    assert dry['data']['would_post_to'] == '/api/predict'
    assert not posted
    result = adapter.run_tool('rhofold', {'sequence': 'AUAUUAUAUAUAU'})
    assert posted == [(8011, '/api/predict', {'sequence': 'AUAUUAUAUAUAU'})]
    assert result['data']['scores']['length'] == 13
    assert result['data']['artifacts'] == [dict(worker='rhofold', job_id='job-1', name='model.pdb')]
    assert result['provenance']['endpoint'] == '/api/predict'


def test_discovery_failure_does_not_submit_or_crash(monkeypatch):
    monkeypatch.setattr(adapter, '_get', lambda *a, **kw: (200, '{"paths":{"/api/stop":{"post":{}}}}'))
    monkeypatch.setattr(adapter, '_post', lambda *a: pytest.fail('must not submit'))
    assert adapter.run_tool('rhofold', {'sequence': 'ACGU'})['ok'] is False
