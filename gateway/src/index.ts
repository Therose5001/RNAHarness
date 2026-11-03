/**
 * Gateway bootstrap. P0 runs the HTTP channel; BioClaw's chat adapters can
 * be registered here later (they share the Channel interface).
 */
import { resolve } from 'node:path';
import { HttpChannel } from './channels/http.ts';
import { Gateway } from './gateway.ts';
import { OpenHarnessOneShot } from './kernel.ts';
import { logger } from './logger.ts';
import { createApp } from './server.ts';
import { SessionStore } from './sessions.ts';

const port = Number(process.env.GATEWAY_PORT ?? 8787);
const host = process.env.GATEWAY_HOST ?? '127.0.0.1';
const dataDir = resolve(process.env.GATEWAY_DATA_DIR ?? 'gateway-data');

const kernelCommand = process.env.OPENHARNESS_CMD ?? 'oh';
const kernelArgs = parseArgs(process.env.OPENHARNESS_ARGS);
const kernel = new OpenHarnessOneShot({
  command: kernelCommand,
  args: kernelArgs,
  timeoutMs: Number(process.env.KERNEL_TIMEOUT_MS ?? 10 * 60 * 1000),
});
const sessions = new SessionStore(dataDir);

const holder: { gateway?: Gateway } = {};
const channel = new HttpChannel(async (chatJid, msg) => {
  await holder.gateway?.handleInbound(chatJid, msg.content);
});
const gateway = new Gateway(channel, kernel, sessions, {
  ackText: process.env.GATEWAY_ACK_TEXT ?? '收到，正在处理…',
});
holder.gateway = gateway;

await channel.connect();
const server = createApp({ gateway, channel, kernelCommand });
server.listen(port, host, () => {
  logger.info(`gateway listening on http://${host}:${port}`, { dataDir, kernel: kernelCommand });
});

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    logger.info(`received ${signal}, shutting down`);
    server.close(() => void channel.disconnect().finally(() => process.exit(0)));
  });
}

function parseArgs(raw: string | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    logger.warn('OPENHARNESS_ARGS is not a JSON array; ignoring');
    return [];
  }
}
