/**
 * Kernel adapter. The gateway only knows `Kernel.run(req) -> AsyncIterable`.
 *
 * P0 ships the one-shot implementation (PLAN.md §2.4): spawn
 * `oh -p "<prompt>" --output-format stream-json` and parse its NDJSON events.
 * P1 can swap in the stdin worker behind the same interface.
 */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { logger } from './logger.ts';
import type { KernelEvent, ResearchArtifact } from './types.ts';

export interface KernelRequest {
  prompt: string;
  /** Stable per-chat id; the one-shot backend does not use it yet. */
  sessionId: string;
  /** Working directory for the kernel process (artifacts land here). */
  workspace: string;
}

export interface Kernel {
  run(req: KernelRequest, signal?: AbortSignal): AsyncIterable<KernelEvent>;
}

export interface OpenHarnessOptions {
  command?: string;
  /** Extra CLI args placed before the prompt. */
  args?: string[];
  env?: Record<string, string>;
  timeoutMs?: number;
  maxTurns?: number;
}

export class OpenHarnessOneShot implements Kernel {
  private readonly command: string;
  private readonly args: string[];
  private readonly env: Record<string, string>;
  private readonly timeoutMs: number;

  constructor(opts: OpenHarnessOptions = {}) {
    this.command = opts.command ?? process.env.OPENHARNESS_CMD ?? 'oh';
    this.args = opts.args ?? [];
    this.env = opts.env ?? {};
    this.timeoutMs = opts.timeoutMs ?? 10 * 60 * 1000;
  }

  async *run(req: KernelRequest, signal?: AbortSignal): AsyncIterable<KernelEvent> {
    const argv = [
      ...this.args,
      '-p', req.prompt,
      '--output-format', 'stream-json',
    ];
    logger.info('kernel spawn', { command: this.command, session: req.sessionId, cwd: req.workspace });

    const child = spawn(this.command, argv, {
      cwd: req.workspace,
      env: { ...process.env, ...this.env },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stderrTail = '';
    // Boxed so TypeScript does not narrow it to null across the callback.
    const spawnState: { error: Error | null } = { error: null };
    child.stderr?.on('data', (chunk: Buffer) => {
      stderrTail = (stderrTail + chunk.toString()).slice(-4000);
      logger.debug('kernel stderr', chunk.toString().trimEnd());
    });
    child.on('error', (err: Error) => {
      spawnState.error = err;
    });

    // Attach before consuming stdout: 'close' can fire while we are still
    // draining the last lines, and a listener added afterwards would hang.
    const exitPromise = new Promise<number | null>((resolve) => {
      child.once('close', (code) => resolve(code));
      child.once('error', () => resolve(-1));
    });

    const timer = setTimeout(() => {
      child.kill('SIGKILL');
    }, this.timeoutMs);
    const onAbort = () => child.kill('SIGKILL');
    signal?.addEventListener('abort', onAbort, { once: true });

    let finalText = '';
    try {
      if (!child.stdout) {
        yield {
          type: 'error',
          message: spawnState.error?.message ?? `could not start kernel: ${this.command}`,
          recoverable: false,
        };
        return;
      }
      const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
      for await (const line of lines) {
        const event = parseKernelLine(line);
        if (!event) continue;
        if (event.type === 'assistant_complete' && event.text) finalText = event.text;
        yield event;
      }
      const exitCode = await exitPromise;
      if (spawnState.error) {
        yield { type: 'error', message: spawnState.error.message, recoverable: false };
        return;
      }
      if (exitCode !== 0) {
        yield {
          type: 'error',
          message: stderrTail.trim() || `kernel exited with code ${exitCode}`,
          recoverable: false,
        };
      } else {
        yield { type: 'done', text: finalText };
      }
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
  }
}

/** Parse one line of `--output-format stream-json`. Unknown shapes are dropped. */
export function parseKernelLine(line: string): KernelEvent | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  let obj: Record<string, unknown>;
  try {
    obj = JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    return null;
  }
  const type = String(obj['type'] ?? '');
  const str = (key: string) => (typeof obj[key] === 'string' ? (obj[key] as string) : '');
  switch (type) {
    case 'assistant_delta':
      return { type, text: str('text') };
    case 'assistant_complete':
      return { type, text: str('text') };
    case 'system':
    case 'status':
      return { type: 'status', message: str('message') };
    case 'tool_started':
      return { type, tool_name: str('tool_name'), tool_input: obj['tool_input'] };
    case 'tool_completed': {
      const artifacts: ResearchArtifact[] = [];
      if (Array.isArray(obj['artifacts'])) {
        for (const item of obj['artifacts'].slice(0, 4)) {
          if (item && item.worker === 'rhofold' && typeof item.job_id === 'string'
              && /^[A-Za-z0-9_-]{1,100}$/.test(item.job_id)
              && typeof item.name === 'string' && /^[A-Za-z0-9_-]{1,100}\.pdb$/.test(item.name)) {
            artifacts.push({ worker: item.worker, job_id: item.job_id, name: item.name });
          }
        }
      }
      return {
        type,
        tool_name: str('tool_name'),
        output: str('output'),
        is_error: obj['is_error'] === true,
        artifacts,
      };
    }
    case 'error':
      return {
        type,
        message: str('message'),
        recoverable: obj['recoverable'] !== false,
      };
    default:
      return null;
  }
}
