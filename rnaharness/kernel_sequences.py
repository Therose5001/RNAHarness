"""Exact RNA references extracted from source messages, including short RNAs."""
import difflib
import re
from . import kernel_catalog

# --------------------------------------------------------------------------
# exact sequence references
# --------------------------------------------------------------------------
_SEQ_RE = re.compile(r"(?<![A-Za-z0-9])[ACGUacgu]{4,}(?![A-Za-z0-9])")


def register_refs(prompt: str) -> dict[str, str]:
    """Deterministically extract exact RNA strings from the ORIGINAL prompt."""
    if '<current_message>\n' in prompt:
        prompt = prompt.rsplit('<current_message>\n', 1)[1].rsplit('\n</current_message>', 1)[0]
    refs: dict[str, str] = {}
    seen: set[str] = set()
    for m in _SEQ_RE.finditer(prompt):
        seq = m.group(0).upper()
        if seq in seen:
            continue
        seen.add(seq)
        refs["seq:%d" % (len(refs) + 1)] = seq
    return refs


def ref_hint(refs: dict[str, str]) -> str:
    lines_head = [
        "\n\n【工具目录】当前注册 %d 个工具；lab_<name> 会真实提交计算作业。"
        % len(kernel_catalog.TOOLS_SPEC),
        "本机当前不可用的 worker：%s（不要调用，也不要编造其输出）。"
        % (", ".join(kernel_catalog.unavailable_workers()) or "无"),
    ]
    base = "\n".join(lines_head)
    if not refs:
        return base
    lines = [
        "\n\n【精确序列引用（重要）】调用 rna_fold / rna_structure / seed_score 时必须使用下面的 ref ID，"
        "不要在参数里抄写序列原文（模型抄写长序列会漏字符）："
    ]
    for key, seq in refs.items():
        lines.append("- %s：%d nt，前缀 %s… ，后缀 …%s"
                     % (key, len(seq), seq[:8], seq[-8:]))
    lines.append("工具会按 ref ID 从原始提问中取回精确序列。")
    return base + "\n" + "\n".join(lines)


def _repair_sequence(raw: str, refs: dict[str, str]) -> tuple[str, str | None, int]:
    """Second line of defence: fix a near-miss copy of a registered sequence.

    Uses a real alignment (difflib), because a single dropped base shifts every
    subsequent position: naive zip-based comparison would report a huge
    distance and refuse to repair exactly the error we care about.
    """
    if not raw or not refs:
        return raw, None, 0
    best_key, best_dist = None, 10 ** 9
    for key, exact in refs.items():
        sm = difflib.SequenceMatcher(None, raw, exact, autojunk=False)
        # edit-ish distance from matching blocks
        dist = len(raw) + len(exact) - 2 * sum(b.size for b in sm.get_matching_blocks())
        if dist < best_dist:
            best_key, best_dist = key, dist
    if best_key and best_dist <= max(2, len(refs[best_key]) // 10):
        return refs[best_key], best_key, best_dist
    return raw, None, best_dist if best_key else 0


def _resolve(args: dict, value_key: str, ref_key: str,
             refs: dict[str, str]) -> tuple[str, dict]:
    ref = str(args.get(ref_key) or "").strip()
    raw = str(args.get(value_key) or "").strip().upper()
    if ref:
        if ref not in refs:
            raise KeyError("unknown %s %r" % (ref_key, ref))
        return refs[ref], {value_key + "_source": ref}
    if raw:
        repaired, used, dist = _repair_sequence(raw, refs)
        if used:
            return repaired, {value_key + "_source": used,
                             value_key + "_repaired": True,
                             value_key + "_edit_distance": dist}
        return repaired, {value_key + "_source": "llm_copied"}
    raise ValueError("missing %s or %s" % (value_key, ref_key))
