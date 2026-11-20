export type DataRecord = Record<string, unknown>;

export interface Insight {
  tool?: string;
  data?: DataRecord;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  insights?: Insight[];
  memory_turn_id?: string | null;
  memory_context?: MemoryContext | null;
}

export interface MemoryContext {
  status: 'provided' | 'none' | 'not_forwarded';
  recent_turns: number;
  summary_turns: number;
  summary_refresh_failed?: boolean;
  excerpts: { date: string; source: string; question: string }[];
}

export interface Conversation {
  id: string;
  conversationId?: string;
  agentMode?: 'ibp' | 's4' | 'maintenance' | 'scenario' | 'migration';
  title: string;
  updatedAt: string;
  messages: ChatMessage[];
}

export interface AskResponse {
  reply: string;
  insights: Insight[];
  conversation_id: string;
  memory_turn_id?: string | null;
  memory_context?: MemoryContext | null;
}
