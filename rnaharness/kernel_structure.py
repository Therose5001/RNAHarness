"""Execute an explicit single-sequence PDB request with an evidence-only receipt."""
import json
import re

from .kernel_execution import result_artifacts


def structure_ref(prompt, refs):
    # Ignore history and quoted source material. Ambiguity stays with the planner.
    text = prompt.rsplit('<current_message>\n', 1)[-1].rsplit('\n</current_message>', 1)[0]
    if '当前用户消息：\n' in text:
        text = text.split('当前用户消息：\n', 1)[1]
    if len(text) > 600 or re.search(
            r'不|别|无需|仅查|只查|查找|检索|如何|怎么|能否|是否|介绍|解释|已有|历史|示例|演示|'
            r'原理|教程|假设|如果|文献|比较|对比|查证|多个|对接|打分|\b(?:not|never|only|explain|how|can|example)\b',
            text, re.I):
        return None
    if not re.search(r'(?:输出|生成|预测|计算|给我|导出).{0,20}(?:pdb|三维|3d)|'
                     r'(?:pdb|三维|3d).{0,20}(?:输出|生成|预测|计算|导出)|'
                     r'\b(?:predict|generate|export)\b.{0,60}\bpdb\b', text, re.I):
        return None
    seqs = set(re.findall(r'(?<![A-Za-z0-9])[ACGUacgu]{4,}(?![A-Za-z0-9])', text))
    seqs = {s.upper() for s in seqs}
    if len(seqs) != 1:
        return None
    return next((key for key, seq in refs.items() if seq in seqs), None)


def structure_receipt(result):
    data = result.get('data') or {}
    artifacts = result_artifacts(result)
    if not result.get('ok') or not artifacts:
        return None
    lines = ['RhoFold+ 三维结构预测完成。',
             '序列长度：%s nt。' % data['sequence_nt'],
             '作业编号：' + str(data['job_id']),
             'PDB产物：' + '、'.join(a['name'] for a in artifacts)]
    scores = data.get('scores') or {}
    # Only copy values provided by the worker; never derive a confidence label.
    if isinstance(scores, dict):
        for key in ('plddt', 'mean_plddt'):
            if key in scores:
                lines.append('%s：%s' % (key, scores[key]))
    lines.append('这是模型预测结构，并非实验测定；附件送达以渠道回执为准。')
    return '\n'.join(lines)


def run_structure(ref, refs, execute, emit):
    args = {'sequence_ref': ref}
    emit({'type': 'status', 'message': '正在提交 RhoFold+ 三维结构预测。'})
    emit({'type': 'tool_started', 'tool_name': 'rna_structure', 'tool_input': args})
    result = execute('rna_structure', args, refs)
    text = structure_receipt(result)
    emit({'type': 'tool_completed', 'tool_name': 'rna_structure',
          'output': json.dumps(result, ensure_ascii=False)[:4000],
          'is_error': not bool(text), 'artifacts': result_artifacts(result) if text else []})
    if not text:
        error = result.get('error') or {}
        emit({'type': 'error', 'message': 'RNA_COMPUTE_FAILED: ' + str(error.get('code', 'MISSING_PDB')),
              'recoverable': False})
        return 1
    emit({'type': 'assistant_complete', 'text': text})
    emit({'type': 'done', 'text': text})
    return 0
