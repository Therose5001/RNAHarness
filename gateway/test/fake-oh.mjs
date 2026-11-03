// Stand-in for `oh -p ... --output-format stream-json` in gateway E2E tests.
// It ignores argv and emits one complete turn.
const emit = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

emit({ type: 'system', message: 'fake kernel' });
emit({ type: 'tool_started', tool_name: 'mcp__lab_mcp__lab_run', tool_input: { tool: 'vienna' } });
emit({ type: 'tool_completed', tool_name: 'mcp__lab_mcp__lab_run', output: '{"ok":true}', is_error: false });
emit({ type: 'assistant_complete', text: '折叠完成：MFE -3.2 kcal/mol' });
