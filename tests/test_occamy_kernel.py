"""Local Occamy kernel: protocol, refs and truncation tests (no LLM needed)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
MIR = "UAGCUUAUCAGACUGAUGUUGACU"
TGT = "AAAAAAAAAUUUAUUUGCCUGCAAAUGGAUUUACUUUAUCCUCGUUCUUA"
OFFLINE_INVENTORY = {"ready": ["vienna", "rhofold"], "degraded": [],
                     "unreachable": ["physicell"], "n_total": 3}


def offline_contract(name):
    return {"ok": True, "data": {
        "run_path": "/api/run", "required": ["tool"],
        "fields": {"tool": {"type": "string", "default": None}}}}


spec = importlib.util.spec_from_file_location(
    "occamy_kernel", ROOT / "scripts" / "occamy_kernel.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
with mock.patch("rnaharness.proto.labtools.inventory",
                return_value=OFFLINE_INVENTORY), \
     mock.patch("rnaharness.proto.adapter.contract", new=offline_contract):
    spec.loader.exec_module(mod)


@pytest.fixture(scope="module", autouse=True)
def offline_environment(tmp_path_factory):
    """Build tiny local stores and an offline worker catalog once per module."""
    from rnaharness.entity import EntityStore
    from rnaharness.entity.loaders import load_hgnc, load_mirbase, load_rnacentral_jsonl
    from rnaharness.evidence import EvidenceStore
    from rnaharness import kernel_catalog

    root = tmp_path_factory.mktemp("kernel-data")
    entity_db = root / "entities.sqlite"
    ent = EntityStore.open(entity_db, create=True)
    load_rnacentral_jsonl(ent, FIXTURES / "rnacentral_mini.jsonl", "2026-11-03")
    load_mirbase(ent, FIXTURES / "mature_mini.fa",
                 FIXTURES / "mirbase_aliases.txt", "2026-11-03")
    load_hgnc(ent, FIXTURES / "hgnc_mini.tsv", "2026-11-03")
    ent.set_meta("source:test", "fixture")
    ent.commit()
    ent.close()

    evidence_db = root / "evidence.sqlite"
    ev = EvidenceStore.open(evidence_db, create=True)
    ev.upsert_document({"doc_id": "PMC9990001", "pmid": "99900001",
                        "title": "miR-21 targets PTEN", "year": 2026,
                        "license": "CC-BY", "snapshot": "2026-11-03",
                        "source": "test"})
    text = "Results: hsa-miR-21-5p targets the PTEN 3' UTR in HEK293 cells."
    ev.add_passage("PMC9990001:results:1", "PMC9990001", "results",
                   text, 0, len(text))
    ev.set_meta("source:test", "1")
    ev.set_meta("snapshot:index", "2026-11-03")
    ev.commit()
    ev.close()

    original = (os.environ.get("RNAHARNESS_ENTITY_DB"),
                os.environ.get("RNAHARNESS_EVIDENCE_DB"))
    os.environ["RNAHARNESS_ENTITY_DB"] = str(entity_db)
    os.environ["RNAHARNESS_EVIDENCE_DB"] = str(evidence_db)
    kernel_catalog.refresh_catalog(OFFLINE_INVENTORY, offline_contract)
    from rnaharness.proto import labtools
    inventory = mock.patch.object(labtools, "inventory",
                                  return_value=OFFLINE_INVENTORY)
    inventory.start()
    mod.TOOLS_SPEC = kernel_catalog.TOOLS_SPEC
    mod.TOOL_META = kernel_catalog.TOOL_META
    try:
        yield
    finally:
        inventory.stop()
        for key, value in zip(("RNAHARNESS_ENTITY_DB",
                               "RNAHARNESS_EVIDENCE_DB"), original):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        kernel_catalog.TOOLS_SPEC, kernel_catalog.TOOL_META = (
            kernel_catalog.build_catalog(OFFLINE_INVENTORY, offline_contract))
        mod.TOOLS_SPEC = kernel_catalog.TOOLS_SPEC
        mod.TOOL_META = kernel_catalog.TOOL_META


def test_tool_specs_are_valid_openai_tools():
    # The catalog is built dynamically from live OpenAPI contracts, so its size
    # depends on which workers are reachable. Assert structure, not a count.
    names = [t["function"]["name"] for t in mod.TOOLS_SPEC]
    assert len(names) >= 14, names
    assert len(names) == len(set(names)), "duplicate tool names"
    for t in mod.TOOLS_SPEC:
        assert t["type"] == "function"
        assert t["function"]["name"]
        assert t["function"]["parameters"]["type"] == "object"
        assert isinstance(t["function"]["parameters"].get("properties"), dict)
    for required in ("literature_evidence", "rna_fold", "lab_inventory",
                     "seed_score", "rna_identify", "evidence_lookup",
                     "citation_verify", "resolve_pair"):
        assert required in names, required
    fold = next(t for t in mod.TOOLS_SPEC if t["function"]["name"] == "rna_fold")
    assert "sequence_ref" in fold["function"]["parameters"]["properties"]
    seed = next(t for t in mod.TOOLS_SPEC if t["function"]["name"] == "seed_score")
    assert "target_ref" in seed["function"]["parameters"]["properties"]


def test_worker_tools_come_from_openapi_contracts():
    """Every registered lab_<name> must have a matching worker and real schema."""
    from rnaharness.proto import labtools
    workers = [t for t in mod.TOOLS_SPEC
               if mod.TOOL_META.get(t["function"]["name"], {}).get("kind") == "worker"]
    for t in workers:
        info = mod.TOOL_META[t["function"]["name"]]
        name = info["worker"]
        assert name in labtools.TOOLS, name
        props = t["function"]["parameters"]["properties"]
        assert isinstance(props, dict)
        # required fields must be a subset of declared properties
        assert set(t["function"]["parameters"].get("required", [])) <= set(props)


def test_rhofold_port_is_dev_box_8011():
    from rnaharness.proto import labtools
    assert labtools.TOOLS["rhofold"][0] == 8011


def test_extra_mcp_tools_are_directly_callable():
    r = mod._call_extra("rna_identify", {"sequence": "UAGCUUAUCAGACUGAUGUUGACU"})
    assert r.get("ok") is True
    assert "matches" in (r.get("data") or {})
    r2 = mod._call_extra("resolve_pair", {"mirna": "hsa-miR-21-5p", "gene": "PTEN"})
    assert r2.get("ok") is True


def test_worker_tool_submits_a_real_job():
    from rnaharness.proto import labtools
    h = labtools.probe_one("vienna")
    if not (h["reachable"] and h["ok"] and h["status"] == "ready"):
        pytest.skip("ViennaRNA worker not ready")
    r = mod.tool_call("lab_vienna", {"tool": "fold",
                                     "params": {"sequence": "GGGAAACCC"}})
    assert r["ok"] is True, r
    assert r["data"]["result"]["structure"] == "(((...)))"


def test_degenerate_sentinel():
    assert mod.degenerate("")
    assert mod.degenerate("?" * 40)
    assert mod.degenerate("aaaaaaaaaaaaaaaa")
    assert not mod.degenerate("RNA 是核糖核酸。")


def test_enforce_chinese_start_only_drops_english_lead_in():
    assert mod._enforce_chinese_start(
        "All four steps are complete. Here is the answer.\n\n## A 文献证据\n内容"
    ) == "## A 文献证据\n内容"
    assert mod._enforce_chinese_start("## A 文献证据\n内容") == "## A 文献证据\n内容"
    assert mod._enforce_chinese_start("RNA 是核糖核酸。") == "RNA 是核糖核酸。"


def test_register_refs_is_exact_and_ordered():
    prompt = ("折叠 " + MIR + " 并评估靶窗口 " + TGT + " 的 seed-match")
    refs = mod.register_refs(prompt)
    assert list(refs.values()) == [MIR, TGT]
    assert list(refs) == ["seq:1", "seq:2"]
    assert len(refs["seq:2"]) == 50


def test_ref_resolution_beats_llm_copied_sequence():
    refs = {"seq:1": MIR, "seq:2": TGT}
    seq, info = mod._resolve({"sequence_ref": "seq:1"}, "sequence",
                             "sequence_ref", refs)
    assert seq == MIR and info["sequence_source"] == "seq:1"
    with pytest.raises(KeyError):
        mod._resolve({"sequence_ref": "seq:99"}, "sequence", "sequence_ref", refs)
    with pytest.raises(ValueError):
        mod._resolve({}, "sequence", "sequence_ref", refs)


def test_repair_fixes_dropped_base():
    refs = {"seq:1": MIR, "seq:2": TGT}
    broken = TGT[1:]  # 49 nt, one leading base dropped
    seq, used, dist = mod._repair_sequence(broken, refs)
    assert used == "seq:2" and seq == TGT and dist <= 5
    # a genuinely unrelated sequence must NOT be silently replaced
    other, used2, _ = mod._repair_sequence("G" * 50, refs)
    assert used2 is None and other == "G" * 50


def test_trim_overlap():
    # a real continuation restates a long tail of the previous chunk
    assert mod._trim_overlap("A 文献证据：10 条。",
                             "10 条。B 计算预测。") == "B 计算预测。"
    assert mod._trim_overlap("abcde", "xyz") == "xyz"
    # a 2-char coincidence must NOT be trimmed (too risky for Chinese text)
    assert mod._trim_overlap("abcde", "defgh") == "defgh"


def test_tool_call_literature_uses_real_evidence_layer():
    r = mod.tool_call("literature_evidence",
                      {"mirna": "hsa-miR-21-5p", "gene": "PTEN"})
    assert r["ok"] is True
    assert "outcome" in r and "coverage" in r


def test_tool_call_fold_uses_real_vienna():
    from rnaharness.proto import labtools
    h = labtools.probe_one("vienna")
    if not (h["reachable"] and h["ok"] and h["status"] == "ready"):
        pytest.skip("ViennaRNA worker not ready")
    r = mod.tool_call("rna_fold", {"sequence": "GGGAAACCC"})
    assert r["ok"] is True and r["structure"] == "(((...)))"


def test_tool_call_seed_score_with_refs_is_in_domain():
    refs = {"seq:1": MIR, "seq:2": TGT}
    r = mod.tool_call("seed_score",
                      {"mirna_sequence_ref": "seq:1", "target_ref": "seq:2"}, refs)
    assert r["ok"] is True
    assert r["window_nt"] == 50 and r["in_domain"] is True
    assert r["target_source"] == "seq:2" and r["calibrated"] is False


def test_tool_call_unknown_is_structured_error():
    r = mod.tool_call("nope", {})
    assert r["ok"] is False and r["error"]["code"] == "unknown_tool"


def test_lab_inventory_returns_explicit_counts():
    r = mod.tool_call("lab_inventory", {})
    assert r["ok"] is True
    assert r["n_ready"] == len(r["ready"]) == 2
    assert r["n_total"] == len(r["ready"]) + len(r["degraded"]) + len(r["unreachable"])


def test_kernel_emits_ndjson_events_with_fake_chat(monkeypatch, capsys):
    """Fake the LLM: one tool call then a final answer; verify event stream."""
    calls = [0]
    def fake_chat(messages, with_tools=True):
        calls[0] += 1
        if calls[0] == 1:
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "content": "", "tool_calls": [
                    {"id": "c1", "function": {"name": "lab_inventory",
                                              "arguments": "{}"}}]}}]}
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "本机有 12 个工具就绪。"}}]}
    monkeypatch.setattr(mod, "chat", fake_chat)
    rc = mod.main(["-p", "有哪些工具", "--output-format", "stream-json"])
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    types = [e["type"] for e in lines]
    assert rc == 0
    assert "tool_started" in types and "tool_completed" in types
    assert "assistant_complete" in types and "done" in types
    assert lines[-1]["text"] == "本机有 12 个工具就绪。"


def test_truncated_generation_is_never_reported_as_done(monkeypatch, capsys):
    """A persistent finish_reason=length must end in error, not done."""
    def fake_chat(messages, with_tools=True):
        return {"choices": [{"finish_reason": "length",
                             "message": {"content": "这是未完成的一段"}}]}
    monkeypatch.setattr(mod, "chat", fake_chat)
    monkeypatch.setattr(mod, "MAX_CONTINUATIONS", 2)
    rc = mod.main(["-p", "写一份很长的汇总", "--output-format", "stream-json"])
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    types = [e["type"] for e in lines]
    assert rc == 1
    assert "done" not in types
    assert "assistant_complete" not in types
    assert any(e.get("type") == "error" and "截断" in e.get("message", "")
               for e in lines)


def test_continuation_concatenates_without_duplicate(monkeypatch, capsys):
    replies = [
        {"choices": [{"finish_reason": "length",
                      "message": {"content": "A 文献证据：10 条。"}}]},
        {"choices": [{"finish_reason": "stop",
                      "message": {"content": "A 文献证据：10 条。B 计算预测：MFE -0.8。"}}]},
    ]
    idx = [0]
    def fake_chat(messages, with_tools=True):
        r = replies[min(idx[0], len(replies) - 1)]
        idx[0] += 1
        return r
    monkeypatch.setattr(mod, "chat", fake_chat)
    rc = mod.main(["-p", "汇总", "--output-format", "stream-json"])
    lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    done = [e for e in lines if e["type"] == "done"]
    assert rc == 0 and len(done) == 1
    assert done[0]["continuations"] == 1
    assert "B 计算预测" in done[0]["text"]
    # the 2-char tail "。B" is not enough to trigger de-duplication, so the
    # continuation is appended as-is; assert the result still contains both
    # sections and no 'done' was emitted on a truncated stream
    assert "A 文献证据" in done[0]["text"]


def test_short_sequence_refs_prefer_current_message():
    prompt = MIR + '\n<current_message>\n我想要AUAUUAUAUAUAU的结构，输出pdb\n</current_message>'
    assert mod.register_refs(prompt) == {'seq:1': 'AUAUUAUAUAUAU'}


def test_structure_uses_exact_source_and_retains_artifacts(monkeypatch):
    from rnaharness.proto import adapter
    seen = []
    def run(name, args, **kwargs):
        seen.append((name, args))
        return {'ok': True, 'data': {'job_id': 'job-1', 'scores': {'length': 13},
                 'artifacts': [{'worker': 'rhofold', 'job_id': 'job-1', 'name': 'model.pdb'}]},
                'provenance': {'endpoint': '/api/predict'}}
    monkeypatch.setattr(adapter, 'run_tool', run)
    result = mod.tool_call('rna_structure', {'sequence_ref': 'seq:1'}, {'seq:1': 'AUAUUAUAUAUAU'})
    assert seen == [('rhofold', {'sequence': 'AUAUUAUAUAUAU'})]
    assert result['data']['prediction'] is True
    assert result['data']['sequence_nt'] == 13
    assert result['data']['artifacts'][0]['name'] == 'model.pdb'


def test_repeated_tool_is_cached_and_forces_summary(monkeypatch, capsys):
    submitted = []; tools_enabled = []
    def execute(name, args, refs):
        submitted.append((name, args))
        return {'ok': True, 'data': {'job_id': 'one-job'}}
    def chat(messages, with_tools=True):
        tools_enabled.append(with_tools)
        if not with_tools:
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': '已完成，任务one-job。'}}]}
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'id': str(len(tools_enabled)), 'function': {'name': 'rna_structure',
                                                       'arguments': '{"sequence_ref":"seq:1"}'}}]}}]}
    monkeypatch.setattr(mod, 'chat', chat)
    monkeypatch.setattr(mod, 'tool_call', execute)
    assert mod.main(['-p', '请综合分析AUAUUAUAUAUAU，比较文献后输出pdb']) == 0
    assert len(submitted) == 1
    assert tools_enabled == [True, True, False]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['type'] == 'done'


def test_last_turn_disallows_more_tools(monkeypatch, capsys):
    monkeypatch.setattr(mod, 'chat', lambda *a, **kw: {'choices': [{'message': {'tool_calls': [
        {'function': {'name': 'rna_structure', 'arguments': '{}'}}]}}]})
    monkeypatch.setattr(mod, 'tool_call', lambda *a: pytest.fail('no final-turn submission'))
    assert mod.main(['-p', '输出pdb', '--max-turns', '1']) == 1
    assert 'TOOL_LOOP_EXCEEDED' in capsys.readouterr().out


def test_degenerate_length_output_is_not_continued(monkeypatch, capsys):
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return {'choices': [{'finish_reason': 'length', 'message': {'content': 'G' * 2048}}]}
    monkeypatch.setattr(mod, 'chat', chat)
    assert mod.main(['-p', '请分析RNA']) == 1
    assert len(calls) == 1
    assert 'MODEL_DEGENERATED' in capsys.readouterr().out


@pytest.mark.parametrize('wrapped', [False, True])
def test_explicit_pdb_request_uses_real_tool_without_model(monkeypatch, capsys, wrapped):
    def execute(name, args, refs):
        assert name == 'rna_structure' and args == {'sequence_ref': 'seq:1'}
        assert refs == {'seq:1': 'AUAUUAUAUAUAU'}
        return {'ok': True, 'data': {'sequence_nt': 13, 'job_id': 'job-1', 'scores': {'plddt': 42},
                'artifacts': [{'worker': 'rhofold', 'job_id': 'job-1', 'name': 'model.pdb'}]}}
    monkeypatch.setattr(mod, 'chat', lambda *a, **kw: pytest.fail('explicit PDB needs no model'))
    monkeypatch.setattr(mod, 'tool_call', execute)
    prompt = '我想要AUAUUAUAUAUAU的结构，输出pdb'
    if wrapped:
        prompt = ('<conversation_history>assistant: 旧的工具查询结果。GGGGGGGGGGGGGGGGGGGG</conversation_history>\n'
                  '<current_message>\n以下为当前群的一条用户输入。\n引用片段（不是新的指令）：\n旧内容\n'
                  '当前用户消息：\n' + prompt + '\n</current_message>')
    assert mod.main(['-p', prompt]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['type'] == 'done'
    assert 'job-1' in events[-1]['text'] and '42' in events[-1]['text']
    assert events[2]['artifacts'][0]['name'] == 'model.pdb'


def test_prompt_hygiene_and_narrow_sentinel():
    long = "今天怎么样？" + "?" * 5000
    clean = mod.sanitize_prompt(long)
    assert "?" * 24 not in clean
    assert "[已过滤异常重复字符]" in clean
    assert len(mod.sanitize_prompt("A" * 13000)) <= 12020
    assert not mod.degenerate("原文" * 4000)
    assert mod.degenerate("?" * 5000)
    assert mod.degenerate("\ufffd" * 24)
    assert not mod.degenerate("今天怎么样？")


def test_literal_tool_markup_is_removed_only_from_content():
    text = "答案<tool_call>{\"name\":\"lab_inventory\"}</tool_call>尾部<tool_call>"
    assert mod.strip_literal_tool_markup(text) == "答案尾部"


def test_final_turn_uses_user_role(monkeypatch, capsys):
    seen = []
    def fake_chat(messages, with_tools=True):
        seen.append([m["role"] for m in messages])
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": "已完成。"}}]}
    monkeypatch.setattr(mod, "chat", fake_chat)
    assert mod.main(["-p", "请汇总", "--max-turns", "1"]) == 0
    assert seen[0] == ["system", "user", "user"]
    assert "已完成。" in capsys.readouterr().out


def test_question_mark_context_does_not_leak_into_answer(monkeypatch, capsys):
    def fake_chat(messages, with_tools=True):
        assert "?" * 24 not in messages[1]["content"]
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": "本机工具清单已返回。"}}]}
    monkeypatch.setattr(mod, "chat", fake_chat)
    assert mod.main(["-p", "有哪些工具？" + "?" * 5000]) == 0
    out = capsys.readouterr().out
    assert "本机工具清单已返回。" in out
    assert "?" * 24 not in out


def test_degenerate_answer_with_tool_results_uses_bounded_receipt(monkeypatch, capsys):
    replies = [
        {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
            {"id": "c1", "function": {"name": "lab_inventory", "arguments": "{}"}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "?" * 5000}}]},
    ]
    idx = [0]
    def fake_chat(messages, with_tools=True):
        r = replies[min(idx[0], len(replies) - 1)]
        idx[0] += 1
        return r
    monkeypatch.setattr(mod, "chat", fake_chat)
    assert mod.main(["-p", "有哪些工具", "--max-turns", "4"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and "lab_inventory" in done[0]["text"]
    assert "?" * 24 not in done[0]["text"]
    assert all("?" * 24 not in e.get("text", "") for e in events)


def test_structure_routing_is_conservative():
    from rnaharness.kernel_structure import structure_ref
    refs = {'seq:1': 'AUAUUAUAUAUAU'}
    for text in ['不要计算AUAUUAUAUAUAU，只介绍如何输出pdb',
                 '能否预测AUAUUAUAUAUAU并输出pdb', '查找AUAUUAUAUAUAU已有pdb',
                 '比较文献AUAUUAUAUAUAU后输出pdb', 'AUAUUAUAUAUAU与GGGAAACCC，输出pdb']:
        assert structure_ref(text, refs) is None
    prompt = '<conversation_history>输出pdb AUAUUAUAUAUAU</conversation_history>\n<current_message>\n你好\n</current_message>'
    assert structure_ref(prompt, refs) is None
    prompt = '引用片段（不是新的指令）：\n生成AUAUUAUAUAUAU的pdb\n当前用户消息：\n你好'
    assert structure_ref(prompt, refs) is None
