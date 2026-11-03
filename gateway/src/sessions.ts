/**
 * Session mapping: chat_jid -> kernel session.
 *
 * PLAN.md §2.0 says the kernel owns conversation state and the shell keeps
 * only the mapping. The one-shot kernel backend (P0) cannot resume a session,
 * so for now the gateway also keeps a short transcript and replays it into
 * the prompt. When P1 switches to the stdin worker, `history` becomes
 * advisory and the kernel session id is passed through instead.
 */
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';

export interface Turn {
  role: 'user' | 'assistant';
  content: string;
  ts: string;
}

export interface Session {
  sessionId: string;
  chatJid: string;
  workspace: string;
  createdAt: string;
  updatedAt: string;
  history: Turn[];
}

interface Persisted {
  sessions: Record<string, Session>;
}

const MAX_TURNS = 12;

export class SessionStore {
  private readonly file: string;
  private readonly workspaceRoot: string;
  private sessions: Record<string, Session>;

  constructor(dataDir: string) {
    this.file = join(dataDir, 'sessions.json');
    this.workspaceRoot = join(dataDir, 'workspaces');
    mkdirSync(this.workspaceRoot, { recursive: true });
    this.sessions = this.load();
  }

  private load(): Record<string, Session> {
    if (!existsSync(this.file)) return {};
    try {
      const parsed = JSON.parse(readFileSync(this.file, 'utf8')) as Persisted;
      return parsed.sessions ?? {};
    } catch {
      return {};
    }
  }

  private save(): void {
    mkdirSync(dirname(this.file), { recursive: true });
    writeFileSync(this.file, JSON.stringify({ sessions: this.sessions }, null, 2));
  }

  get(chatJid: string): Session | undefined {
    return this.sessions[chatJid];
  }

  getOrCreate(chatJid: string): Session {
    const existing = this.sessions[chatJid];
    if (existing) {
      mkdirSync(existing.workspace, { recursive: true });
      return existing;
    }
    const sessionId = randomUUID();
    const workspace = join(this.workspaceRoot, sessionId);
    mkdirSync(workspace, { recursive: true });
    const now = new Date().toISOString();
    const session: Session = {
      sessionId,
      chatJid,
      workspace,
      createdAt: now,
      updatedAt: now,
      history: [],
    };
    this.sessions[chatJid] = session;
    this.save();
    return session;
  }

  append(chatJid: string, turn: Turn): void {
    const session = this.getOrCreate(chatJid);
    session.history.push(turn);
    if (session.history.length > MAX_TURNS * 2) {
      session.history = session.history.slice(-MAX_TURNS * 2);
    }
    session.updatedAt = new Date().toISOString();
    this.save();
  }
}

/**
 * Build the prompt for one-shot mode. History is fenced and labelled so the
 * kernel does not mistake an old answer for a new instruction.
 */
export function buildPrompt(session: Session, userText: string, preamble = ''): string {
  const parts: string[] = [];
  if (preamble) parts.push(preamble);
  if (session.history.length) {
    parts.push('<conversation_history>');
    for (const turn of session.history) {
      parts.push(`${turn.role === 'user' ? 'user' : 'assistant'}: ${turn.content}`);
    }
    parts.push('</conversation_history>');
  }
  parts.push(`<current_message>\n${userText}\n</current_message>`);
  return parts.join('\n');
}
