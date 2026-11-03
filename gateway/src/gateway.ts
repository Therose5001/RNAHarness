/**
 * Gateway: the only place that knows both "channels" and "kernel".
 *
 * Responsibilities (PLAN.md §2.0): session mapping, ack/progress, artifacts.
 * It never plans and never calls research tools — that is the kernel's job.
 */
import { randomUUID } from 'node:crypto';
import { logger } from './logger.ts';
import { buildPrompt, type SessionStore, type Turn } from './sessions.ts';
import type { Channel, GatewayEvent, KernelEvent } from './types.ts';
import type { Kernel } from './kernel.ts';

export interface GatewayOptions {
  ackText?: string;
  preamble?: string;
  /** Keep the most recent events per task for late SSE subscribers. */
  eventBufferSize?: number;
}

interface Task {
  id: string;
  chatJid: string;
  startedAt: number;
  events: GatewayEvent[];
  subscribers: Set<(ev: GatewayEvent) => void>;
  finished: boolean;
}

const DEFAULT_PREAMBLE = [
  'You are the RNA research agent. Use the lab_mcp tools for computation.',
  'Never invent tool output. If a tool fails, report the structured error code.',
  'When evidence and prediction disagree, show both; never present a prediction as literature evidence.',
].join(' ');

export class Gateway {
  private readonly tasks = new Map<string, Task>();
  private readonly channel: Channel;
  private readonly kernel: Kernel;
  private readonly sessions: SessionStore;
  private readonly ackText: string;
  private readonly preamble: string;
  private readonly bufferSize: number;

  constructor(
    channel: Channel,
    kernel: Kernel,
    sessions: SessionStore,
    opts: GatewayOptions = {},
  ) {
    this.channel = channel;
    this.kernel = kernel;
    this.sessions = sessions;
    this.ackText = opts.ackText ?? '收到，正在处理…';
    this.preamble = opts.preamble ?? DEFAULT_PREAMBLE;
    this.bufferSize = opts.eventBufferSize ?? 200;
  }

  /** Inbound entry point used by every channel adapter. */
  async handleInbound(chatJid: string, content: string): Promise<string> {
    const task = this.createTask(chatJid);
    this.emit(task, { type: 'task.created', task_id: task.id, chat_jid: chatJid });
    this.emit(task, { type: 'task.ack', task_id: task.id });

    // Ack first: channel users need a sub-second response, not a spinner.
    await this.channel.sendMessage(chatJid, this.ackText).catch((err: unknown) => {
      logger.warn('ack send failed', { chatJid, error: String(err) });
    });

    void this.runTask(task, content);
    return task.id;
  }

  private async runTask(task: Task, content: string): Promise<void> {
    const session = this.sessions.getOrCreate(task.chatJid);
    const prompt = buildPrompt(session, content, this.preamble);
    let finalText = '';
    let failed = false;

    try {
      for await (const event of this.kernel.run({
        prompt,
        sessionId: session.sessionId,
        workspace: session.workspace,
      })) {
        this.mapKernelEvent(task, event);
        if (event.type === 'assistant_complete' && event.text) finalText = event.text;
        if (event.type === 'done' && event.text) finalText = event.text;
        if (event.type === 'error' && !event.recoverable) {
          failed = true;
          this.emit(task, { type: 'task.failed', task_id: task.id, message: event.message });
        }
      }
    } catch (err) {
      failed = true;
      const message = err instanceof Error ? err.message : String(err);
      logger.error('kernel run failed', { task: task.id, error: message });
      this.emit(task, { type: 'task.failed', task_id: task.id, message });
    }

    task.finished = true;
    if (!failed && finalText.trim()) {
      this.sessions.append(task.chatJid, userTurn(content));
      this.sessions.append(task.chatJid, assistantTurn(finalText));
      this.emit(task, { type: 'answer.completed', task_id: task.id, text: finalText });
      await this.channel.sendMessage(task.chatJid, finalText).catch((err: unknown) => {
        logger.warn('answer send failed', { chatJid: task.chatJid, error: String(err) });
      });
    } else if (!failed) {
      const message = '内核没有返回文本结果。';
      this.emit(task, { type: 'task.failed', task_id: task.id, message });
      await this.channel.sendMessage(task.chatJid, message).catch(() => undefined);
    }
    this.closeTask(task);
  }

  private mapKernelEvent(task: Task, event: KernelEvent): void {
    switch (event.type) {
      case 'assistant_delta':
        if (event.text) this.emit(task, { type: 'answer.delta', task_id: task.id, text: event.text });
        break;
      case 'tool_started':
        this.emit(task, { type: 'tool.started', task_id: task.id, tool_name: event.tool_name });
        break;
      case 'tool_completed':
        this.emit(task, {
          type: 'tool.completed',
          task_id: task.id,
          tool_name: event.tool_name,
          is_error: event.is_error,
        });
        break;
      case 'error':
        logger.warn('kernel error event', { task: task.id, message: event.message });
        break;
      default:
        break;
    }
  }

  subscribe(taskId: string, fn: (ev: GatewayEvent) => void): () => void {
    const task = this.tasks.get(taskId);
    if (!task) return () => undefined;
    for (const past of task.events) fn(past);
    task.subscribers.add(fn);
    return () => task.subscribers.delete(fn);
  }

  getTask(taskId: string): GatewayEvent[] | undefined {
    return this.tasks.get(taskId)?.events;
  }

  private createTask(chatJid: string): Task {
    const task: Task = {
      id: randomUUID(),
      chatJid,
      startedAt: Date.now(),
      events: [],
      subscribers: new Set(),
      finished: false,
    };
    this.tasks.set(task.id, task);
    return task;
  }

  private emit(task: Task, event: GatewayEvent): void {
    task.events.push(event);
    if (task.events.length > this.bufferSize) {
      task.events.splice(0, task.events.length - this.bufferSize);
    }
    for (const fn of task.subscribers) {
      try {
        fn(event);
      } catch (err) {
        logger.warn('subscriber threw', { error: String(err) });
      }
    }
  }

  private closeTask(task: Task): void {
    const timer = setTimeout(() => {
      task.subscribers.clear();
      this.tasks.delete(task.id);
    }, 60_000);
    // Do not hold the process open just to drop a finished task.
    timer.unref?.();
  }
}

function userTurn(content: string): Turn {
  return { role: 'user', content, ts: new Date().toISOString() };
}

function assistantTurn(content: string): Turn {
  return { role: 'assistant', content, ts: new Date().toISOString() };
}
