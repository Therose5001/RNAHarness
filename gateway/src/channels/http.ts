/**
 * Minimal HTTP channel for P0.
 *
 * It is deliberately the smallest possible Channel: inbound POSTs, outbound
 * messages kept in a ring buffer for `GET /messages`. BioClaw's adapters
 * (feishu / slack / wecom / ...) implement the same interface and can be
 * copied into this folder without touching the gateway.
 */
import type { Channel, NewMessage } from '../types.ts';

export interface OutboundMessage {
  chat_jid: string;
  text: string;
  ts: string;
}

export class HttpChannel implements Channel {
  readonly name = 'http';
  private connected = false;
  private readonly outbox = new Map<string, OutboundMessage[]>();
  private readonly onInbound: (chatJid: string, msg: NewMessage) => void | Promise<void>;

  constructor(onInbound: (chatJid: string, msg: NewMessage) => void | Promise<void>) {
    this.onInbound = onInbound;
  }

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  isConnected(): boolean {
    return this.connected;
  }

  ownsJid(jid: string): boolean {
    return jid.startsWith('http:');
  }

  async sendMessage(jid: string, text: string): Promise<void> {
    const list = this.outbox.get(jid) ?? [];
    list.push({ chat_jid: jid, text, ts: new Date().toISOString() });
    if (list.length > 200) list.splice(0, list.length - 200);
    this.outbox.set(jid, list);
  }

  messages(jid: string): OutboundMessage[] {
    return [...(this.outbox.get(jid) ?? [])];
  }

  /** Simulate an inbound message (used by the HTTP server and tests). */
  async receive(chatJid: string, content: string): Promise<void> {
    const message: NewMessage = {
      id: globalThis.crypto.randomUUID(),
      chat_jid: chatJid,
      sender: 'http-user',
      sender_name: 'http-user',
      content,
      timestamp: new Date().toISOString(),
      message_type: 'chat',
    };
    await this.onInbound(chatJid, message);
  }
}
