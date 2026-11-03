const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 } as const;
type Level = keyof typeof LEVELS;

const threshold = LEVELS[(process.env.LOG_LEVEL as Level) ?? 'info'] ?? LEVELS.info;

function emit(level: Level, msg: string, extra?: unknown): void {
  if (LEVELS[level] < threshold) return;
  const line = `[${new Date().toISOString()}] ${level.toUpperCase()} ${msg}`;
  const stream = level === 'error' ? process.stderr : process.stdout;
  stream.write(extra === undefined ? line + '\n' : `${line} ${safeJson(extra)}\n`);
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export const logger = {
  debug: (msg: string, extra?: unknown) => emit('debug', msg, extra),
  info: (msg: string, extra?: unknown) => emit('info', msg, extra),
  warn: (msg: string, extra?: unknown) => emit('warn', msg, extra),
  error: (msg: string, extra?: unknown) => emit('error', msg, extra),
};
