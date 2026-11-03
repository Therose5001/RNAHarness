/**
 * Gateway tests with a fake kernel. No OpenHarness install required.
 * Run: node --experimental-strip-types --test test/*.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { Gateway } from '../src/gateway.ts';
import { HttpChannel } from '../src/channels/http.ts';
import { SessionStore, buildPrompt } from '../src/sessions.ts';
import { parseKernelLine, type Kernel } from '../src/kernel.ts';
import type { KernelEvent } from '../src/types.ts';

function tempStore(): SessionStore {
  return new SessionStore(mkdtempSync(join(tmpdir(), 'rna-gw-')));
}

class FakeKernel implements Kernel {
  private readonly events: KernelEvent[];
  constructor(events: KernelEvent[]) {
    this.events = events;
  }
  async *run(_req: { prompt: string }): AsyncIterable<KernelEvent> {
    for (const ev of this.events) yield ev;
  }
}

test('parseKernelLine maps stream-json events and drops junk', () => {
  assert.deepEqual(parseKernelLine('{"type":"assistant_delta","text":"hi"}'),
    { type: 'assistant_delta', text: 'hi' });
  assert.deepEqual(parseKernelLine('{"type":"tool_started","tool_name":"mcp__lab_mcp__lab_run","tool_input":{}}'),
    { type: 'tool_started', tool_name: 'mcp__lab_mcp__lab_run', tool_input: {} });
  assert.equal(parseKernelLine('not json'), null);
  assert.equal(parseKernelLine('{"type":"unknown"}'), null);
});

test('happy path: ack, events, answer, session history', async () => {
  const store = tempStore();
  const channel = new HttpChannel(async () => undefined);
  await channel.connect();
  const kernel = new FakeKernel([
    { type: 'tool_started', tool_name: 'lab_run', tool_input: {} },
    { type: 'tool_completed', tool_name: 'lab_run', output: '{}', is_error: false },
    { type: 'assistant_complete', text: '折叠完成' },
    { type: 'done', text: '折叠完成' },
  ]);
  const gw = new Gateway(channel, kernel, store);
  const taskId = await gw.handleInbound('http:u1', '折叠 ACGUACGU');

  const events = await waitFor(gw, taskId, 'answer.completed');
  const types = events.map((e) => e.type);
  assert.deepEqual(types.slice(0, 3), ['task.created', 'task.ack', 'tool.started']);
  assert.ok(types.includes('answer.completed'));

  const sent = channel.messages('http:u1');
  assert.equal(sent[0]?.text, '收到，正在处理…');
  assert.equal(sent[1]?.text, '折叠完成');

  const session = store.get('http:u1');
  assert.equal(session?.history.length, 2);
  assert.match(session?.history[0]?.content ?? '', /ACGUACGU/);
});

test('kernel error becomes task.failed', async () => {
  const store = tempStore();
  const channel = new HttpChannel(async () => undefined);
  await channel.connect();
  const gw = new Gateway(channel, new FakeKernel([
    { type: 'error', message: 'boom', recoverable: false },
  ]), store);
  const taskId = await gw.handleInbound('http:u2', 'hello');
  const events = await waitFor(gw, taskId, 'task.failed');
  assert.ok(events.some((e) => e.type === 'task.failed'));
});

test('buildPrompt includes history and the current message', () => {
  const store = tempStore();
  const session = store.getOrCreate('http:u3');
  store.append('http:u3', { role: 'user', content: 'previous', ts: '' });
  const prompt = buildPrompt(store.getOrCreate(session.chatJid), 'now', 'PREAMBLE');
  assert.match(prompt, /PREAMBLE/);
  assert.match(prompt, /user: previous/);
  assert.match(prompt, /<current_message>\nnow\n<\/current_message>/);
});

async function waitFor(gw: Gateway, taskId: string, eventual: string, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const events = gw.getTask(taskId);
    if (events?.some((e) => e.type === eventual)) return events;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error(`task ${taskId} never reached ${eventual}`);
}
