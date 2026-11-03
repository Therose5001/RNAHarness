/**
 * HTTP surface of the gateway.
 *
 *   POST /messages                  {chat_jid, content} -> {task_id}
 *   GET  /tasks/:id/events          SSE stream of GatewayEvent
 *   GET  /tasks/:id                 buffered events as JSON
 *   GET  /messages?chat_jid=...     outbound messages for a chat
 *   GET  /health                    liveness + configured kernel
 *
 * Only this port should ever be exposed (PLAN.md §2.3).
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { Gateway } from './gateway.ts';
import type { HttpChannel } from './channels/http.ts';
import { logger } from './logger.ts';

interface Deps {
  gateway: Gateway;
  channel: HttpChannel;
  kernelCommand: string;
}

export function createApp(deps: Deps) {
  const { gateway, channel, kernelCommand } = deps;

  return createServer((req, res) => {
    handle(req, res).catch((err: unknown) => {
      logger.error('request failed', { url: req.url, error: String(err) });
      if (!res.headersSent) json(res, 500, { error: 'internal error' });
      else res.end();
    });
  });

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? '/', 'http://localhost');
    const method = req.method ?? 'GET';

    if (method === 'GET' && url.pathname === '/health') {
      return json(res, 200, {
        ok: true,
        channel: channel.name,
        channel_connected: channel.isConnected(),
        kernel: kernelCommand,
      });
    }

    if (method === 'POST' && url.pathname === '/messages') {
      let body: Record<string, unknown>;
      try {
        body = await readJson(req);
      } catch {
        return json(res, 400, { error: 'invalid JSON body' });
      }
      const chatJid = typeof body['chat_jid'] === 'string' ? body['chat_jid'] : '';
      const content = typeof body['content'] === 'string' ? body['content'] : '';
      if (!chatJid || !content.trim()) {
        return json(res, 400, { error: 'chat_jid and content are required' });
      }
      const taskId = await gateway.handleInbound(chatJid, content);
      return json(res, 202, { task_id: taskId });
    }

    const sseMatch = /^\/tasks\/([^/]+)\/events$/.exec(url.pathname);
    if (method === 'GET' && sseMatch) {
      const taskId = decodeURIComponent(sseMatch[1]!);
      const events = gateway.getTask(taskId);
      if (!events) return json(res, 404, { error: 'unknown task' });
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      });
      const heartbeat = setInterval(() => res.write(': ping\n\n'), 15_000);
      const unsubscribe = gateway.subscribe(taskId, (ev) => {
        res.write(`event: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`);
        if (ev.type === 'answer.completed' || ev.type === 'task.failed') {
          clearInterval(heartbeat);
          unsubscribe();
          res.end();
        }
      });
      req.on('close', () => {
        clearInterval(heartbeat);
        unsubscribe();
      });
      return;
    }

    const taskMatch = /^\/tasks\/([^/]+)$/.exec(url.pathname);
    if (method === 'GET' && taskMatch) {
      const events = gateway.getTask(decodeURIComponent(taskMatch[1]!));
      if (!events) return json(res, 404, { error: 'unknown task' });
      return json(res, 200, { events });
    }

    if (method === 'GET' && url.pathname === '/messages') {
      const chatJid = url.searchParams.get('chat_jid') ?? '';
      if (!chatJid) return json(res, 400, { error: 'chat_jid is required' });
      return json(res, 200, { messages: channel.messages(chatJid) });
    }

    json(res, 404, { error: 'not found' });
  }
}

function json(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > 1_000_000) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}') as Record<string, unknown>);
      } catch (err) {
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
    req.on('error', reject);
  });
}
