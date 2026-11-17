"""Request-local memoization: a model retry must not submit a second lab job."""
import json


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
