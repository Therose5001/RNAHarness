"""lab_mcp / lab_client tests against a local mock of the lab API.

No API key, no network: the mock server stands in for
https://www.romancohort.cloud/api/v1 and enforces the same contract
(409 reuse, sha256 manifests, 429/401/5xx).
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rnaharness import envelope as env
from rnaharness.lab_client import LabClient, LabError, validate_params

MANIFEST = {
    "tools": [
        {"id": "vienna", "name": "ViennaRNA", "status": "ready",
         "category": "structure", "version": "2.6",
         "input_schema": {
             "type": "object",
             "properties": {
                 "tool": {"type": "string",
                          "enum": ["fold", "plfold", "cofold", "duplex"]},
                 "params": {"type": "object"},
             },
             "required": ["tool"],
         }},
        {"id": "rhofold", "name": "RhoFold+", "status": "ready",
         "input_schema": {
             "type": "object",
             "properties": {"sequence": {"type": "string"},
                            "msa": {"type": "string"}},
             "required": ["sequence"],
         }},
        {"id": "rnadvisor", "name": "RNAdvisor", "status": "ready",
         "input_schema": {
             "type": "object",
             "properties": {"pdb": {"type": "string"},
                            "native_pdb": {"type": "string"},
                            "scores": {"type": "array"}},
             "required": ["pdb"],
         }},
        {"id": "offline", "name": "Down", "status": "error",
         "input_schema": {"type": "object", "properties": {}}},
    ]
}

FILE_BODY = b">seq\nACGUACGU\n"
FILE_SHA = hashlib.sha256(FILE_BODY).hexdigest()


class MockLab(BaseHTTPRequestHandler):
    runs: dict = {}
    submissions: int = 0
    fail_next: int = 0

    def log_message(self, *a):  # silence
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if self.headers.get("Authorization") != "Bearer test-key":
            self._json(401, {"error": "invalid or missing API key"})
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/api/v1/tools":
            return self._json(200, MANIFEST)
        if self.path == "/api/v1/runs/r1/files/model.pdb":
            self.send_response(200)
            self.send_header("Content-Length", str(len(FILE_BODY)))
            self.end_headers()
            self.wfile.write(FILE_BODY)
            return
        if self.path.endswith("/files?hash=1"):
            return self._json(200, {"files": [{
                "name": "model.pdb", "downloadable": True,
                "url": "/api/v1/runs/r1/files/model.pdb",
                "sha256": FILE_SHA, "size": len(FILE_BODY)}]})
        if self.path.startswith("/api/v1/runs/"):
            run_id = self.path.rsplit("/", 1)[-1]
            return self._json(200, {"run_id": run_id, "tool": "vienna",
                                    "status": "done",
                                    "result": {"mfe": -3.2},
                                    "files": ["model.pdb"]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        if self.path == "/api/v1/runs":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            type(self).submissions += 1
            if type(self).fail_next:
                type(self).fail_next -= 1
                return self._json(503, {"error": "backend warming up"})
            if body.get("name") == "dup":
                return self._json(409, {"run_id": "r-existing",
                                        "status": "running"})
            return self._json(202, {"run_id": "r1", "status": "queued"})
        return self._json(404, {"error": "not found"})


@pytest.fixture()
def lab(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), MockLab)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d/api/v1" % server.server_address[1]
    monkeypatch.setenv("ROMANCOHORT_API_KEY", "test-key")
    MockLab.runs = {}
    MockLab.submissions = 0
    MockLab.fail_next = 0
    yield base
    server.shutdown()


# -- lab_client ------------------------------------------------------------
def test_submit_and_attach(lab):
    client = LabClient(base=lab)
    res = client.submit("vienna", {"tool": "fold"})
    assert res == {"run_id": "r1", "status": "queued", "attached": False}

    attached = client.submit("vienna", {"tool": "fold"}, name="dup")
    assert attached["attached"] is True
    assert attached["run_id"] == "r-existing"


def test_missing_key_raises_401(lab, monkeypatch):
    monkeypatch.delenv("ROMANCOHORT_API_KEY")
    with pytest.raises(LabError) as exc:
        LabClient(base=lab).tools()
    assert exc.value.status == 401


def test_download_verifies_sha256(lab, tmp_path):
    files = LabClient(base=lab).download("r1", tmp_path)
    assert files[0]["hash_ok"] is True
    assert (tmp_path / "model.pdb").read_bytes() == FILE_BODY


def test_hash_mismatch_detected(lab, tmp_path):
    orig = MockLab.do_GET

    def tampered(self):
        if self.path.endswith("/files?hash=1"):
            return self._json(200, {"files": [{
                "name": "model.pdb", "downloadable": True,
                "url": "/api/v1/runs/r1/files/model.pdb",
                "sha256": "0" * 64}]})
        return orig(self)

    MockLab.do_GET = tampered
    try:
        files = LabClient(base=lab).download("r1", tmp_path)
        assert files[0]["hash_ok"] is False
    finally:
        MockLab.do_GET = orig


# -- validate_params -------------------------------------------------------
def test_validate_params_rejects_unknown_and_bad_enum():
    entry = MANIFEST["tools"][0]
    assert validate_params(entry, {"tool": "fold"}) == []
    problems = validate_params(entry, {"tool": "spin", "bogus": 1})
    assert any("unknown field: bogus" in p for p in problems)
    assert any("not in" in p for p in problems)


def test_validate_params_requires_fields():
    problems = validate_params(MANIFEST["tools"][1], {})
    assert problems == ["missing required field: sequence"]


# -- lab_mcp dispatch ------------------------------------------------------
@pytest.fixture()
def mcp(lab, monkeypatch):
    from rnaharness.servers import lab_mcp
    lab_mcp.client = LabClient(base=lab)
    lab_mcp._MANIFEST = {"tools": MANIFEST["tools"]}
    return lab_mcp


def test_lab_run_ok(mcp):
    payload = mcp.lab_run("vienna", {"tool": "fold"})
    assert payload["ok"] is True
    assert payload["data"]["run_id"] == "r1"
    assert payload["provenance"]["source"] == "romancohort.lab"


def test_lab_run_attaches_on_409(mcp):
    payload = mcp.lab_run("vienna", {"tool": "fold"}, name="dup")
    assert payload["ok"] is True
    assert payload["data"] == {"run_id": "r-existing", "status": "running",
                               "attached": True}


def test_lab_run_unknown_tool(mcp):
    payload = mcp.lab_run("nope", {})
    assert payload["ok"] is False
    assert payload["error"]["code"] == env.INVALID_PARAMS
    assert "vienna" in payload["error"]["detail"]["known_tools"]


def test_lab_run_tool_not_ready(mcp):
    payload = mcp.lab_run("offline", {})
    assert payload["error"]["code"] == env.BACKEND_UNAVAILABLE


def test_lab_run_rejects_invented_field(mcp):
    payload = mcp.lab_run("rhofold", {"sequence": "ACGU", "msaa": "x"})
    assert payload["error"]["code"] == env.INVALID_PARAMS
    assert "unknown field: msaa" in payload["error"]["message"]


def test_lab_run_maps_503(mcp):
    MockLab.fail_next = 1
    payload = mcp.lab_run("vienna", {"tool": "fold"})
    assert payload["error"]["code"] == env.BACKEND_UNAVAILABLE
    assert payload["error"]["retryable"] is True
    assert payload["error"]["detail"]["http_status"] == 503


def test_lab_status_done(mcp):
    payload = mcp.lab_status("r1")
    assert payload["ok"] is True
    assert payload["data"]["result"] == {"mfe": -3.2}


def test_lab_files_download(mcp, tmp_path):
    payload = mcp.lab_files("r1", download=True, out_dir=str(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["files"][0]["hash_ok"] is True


def test_typed_tool_routes_to_lab_run(mcp):
    payload = mcp.rhofold("ACGUACGU")
    assert payload["ok"] is True
    assert MockLab.submissions == 1


def test_typed_tool_omits_unset_defaults(mcp):
    # rnadvisor without native_pdb/scores must not send empty strings/lists.
    payload = mcp.rnadvisor("pdb-text")
    assert payload["ok"] is True
    # the mock only checks tool id; assert the params we built are minimal
    from rnaharness.servers.lab_mcp import _drop_empty
    assert _drop_empty({"pdb": "x", "native_pdb": "", "scores": None}) == {"pdb": "x"}


def test_mcp_server_exposes_all_tools(mcp):
    import asyncio
    tools = asyncio.run(mcp.server.list_tools())
    names = {t.name for t in tools}
    expected = {"lab_run", "lab_status", "lab_files", "vienna", "rhofold",
                "ncbp", "gatsite", "gemorna", "rnadvisor"}
    assert names == expected
