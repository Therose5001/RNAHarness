"""Request-local memoization and bounded output hygiene for the kernel."""
import json
import re


def result_artifacts(result):
    if not isinstance(result, dict) or not result.get('ok'):
        return []
    data = result.get('data') or {}
    return data.get('artifacts', []) if isinstance(data, dict) else []


class ToolRunCache:
    def __init__(self, execute, refs):
        self.execute = execute
        self.refs = refs
        self.results = {}

    def run(self, name, args):
        normalized = dict(args)
        canonical_name = name
        # The high-level and raw 3D entry points denote the same submission.
        if name in {'rna_structure', 'lab_rhofold', 'rna_fold'}:
            ref = normalized.pop('sequence_ref', None)
            if ref in self.refs:
                normalized['sequence'] = self.refs[ref]
            if isinstance(normalized.get('sequence'), str):
                normalized['sequence'] = normalized['sequence'].strip().upper()
            if name == 'rna_structure':
                canonical_name = 'lab_rhofold'
        key = json.dumps([canonical_name, normalized], sort_keys=True, ensure_ascii=False)
        if key in self.results:
            return self.results[key], True
        result = self.execute(name, args, self.refs)
        self.results[key] = result
        return result, False


_JUNK_RUN = re.compile(r"[?\uff1f\ufffd]{24,}|(.)\1{499,}", re.DOTALL)
_LITERAL_TOOL_BLOCK = re.compile(r"<tool_call\b.*?</tool_call\s*>", re.IGNORECASE | re.DOTALL)
_LITERAL_TOOL_OPEN = re.compile(r"<tool_call\b.*", re.IGNORECASE | re.DOTALL)


def sanitize_prompt(text: str) -> str:
    """Remove repeated junk and bound context before it reaches the model."""
    s = _JUNK_RUN.sub("[\u5df2\u8fc7\u6ee4\u5f02\u5e38\u91cd\u590d\u5b57\u7b26]", text or "")
    if len(s) > 12000:
        s = s[:12000] + "\n[\u8f93\u5165\u5df2\u622a\u65ad]"
    return s


def strip_literal_tool_markup(text: str) -> str:
    """Remove literal tool tags from model content; real tool_calls are untouched."""
    s = _LITERAL_TOOL_BLOCK.sub("", text or "")
    return _LITERAL_TOOL_OPEN.sub("", s).strip()


def deterministic_summary(tool_runs: list, artifacts: list, reason: str) -> str:
    """A bounded receipt built only from tool events already emitted this run."""
    names = list(dict.fromkeys(str(run[0]) for run in tool_runs))
    lines = [
        "\u672c\u8f6e\u5de5\u5177\u8c03\u7528\u5df2\u6709\u7ed3\u679c\uff0c\u4f46\u6a21\u578b\u8f93\u51fa\u4e0d\u53ef\u7528\uff1b\u4ee5\u4e0b\u4ec5\u56de\u6267\u5df2\u53d1\u751f\u7684\u5de5\u5177\u4e8b\u4ef6\u3002",
        "\u5df2\u8c03\u7528\u5de5\u5177\uff1a" + ("\u3001".join(names) if names else "\u65e0") + "\u3002",
        "\u539f\u56e0\uff1a" + reason.rstrip("\u3002") + "\u3002",
    ]
    if any(not bool(run[1]) for run in tool_runs):
        lines.append("\u5176\u4e2d\u6709\u5de5\u5177\u8fd4\u56de\u5931\u8d25\uff1b\u5931\u8d25\u8be6\u60c5\u4ee5\u672c\u8f6e tool_completed \u4e8b\u4ef6\u4e3a\u51c6\u3002")
    pdb_names = []
    for item in artifacts:
        if isinstance(item, dict) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.pdb", str(item.get("name") or "")):
            pdb_names.append(str(item["name"]))
    pdb_names = sorted(dict.fromkeys(pdb_names))
    if pdb_names:
        lines.append("\u5b9e\u9645\u8fd4\u56de\u7684 PDB \u4ea7\u7269\uff1a" + "\u3001".join(pdb_names) +
                    "\uff1b\u9644\u4ef6\u4ea4\u4ed8\u7531\u5fae\u4fe1\u4fa7\u6309\u73b0\u6709\u7ba1\u7ebf\u786e\u8ba4\u3002")
    else:
        lines.append("\u672c\u8f6e\u6ca1\u6709\u6536\u5230\u53ef\u4ea4\u4ed8\u7684 PDB \u4ea7\u7269\u3002")
    text = "\n".join(lines)
    return text[:1800] + "\u2026[\u56de\u6267\u5df2\u622a\u65ad]" if len(text) > 1800 else text
