/**
 * Channel contract. Shape is kept identical to BioClaw's
 * `_refs/BioClaw_src/src/types.ts` (Channel, NewMessage) so its adapters can
 * be dropped into `src/channels/` later without a rewrite.
 */
export interface NewMessage {
  id: string;
  chat_jid: string;
  sender: string;
  sender_name: string;
  content: string;
  timestamp: string;
  is_from_me?: boolean;
  message_type?: 'chat' | 'control' | 'system';
}

export interface Channel {
  name: string;
  connect(): Promise<void>;
  sendMessage(jid: string, text: string): Promise<void>;
  isConnected(): boolean;
  ownsJid(jid: string): boolean;
  disconnect(): Promise<void>;
  setTyping?(jid: string, isTyping: boolean): Promise<void>;
  sendImage?(jid: string, imagePath: string, caption?: string): Promise<void>;
}

export type OnInboundMessage = (chatJid: string, message: NewMessage) => void | Promise<void>;

/** Kernel events, normalised over whatever the kernel actually emits. */
export type KernelEvent =
  | { type: 'system'; message: string }
  | { type: 'assistant_delta'; text: string }
  | { type: 'assistant_complete'; text: string }
  | { type: 'tool_started'; tool_name: string; tool_input: unknown }
  | { type: 'tool_completed'; tool_name: string; output: string; is_error: boolean }
  | { type: 'status'; message: string }
  | { type: 'error'; message: string; recoverable: boolean }
  | { type: 'done'; text: string };

/** Gateway task lifecycle, as seen by channel clients. */
export type GatewayEvent =
  | { type: 'task.created'; task_id: string; chat_jid: string }
  | { type: 'task.ack'; task_id: string }
  | { type: 'tool.started'; task_id: string; tool_name: string }
  | { type: 'tool.completed'; task_id: string; tool_name: string; is_error: boolean }
  | { type: 'answer.delta'; task_id: string; text: string }
  | { type: 'answer.completed'; task_id: string; text: string }
  | { type: 'task.failed'; task_id: string; message: string };
