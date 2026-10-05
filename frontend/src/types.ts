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
}

export interface Conversation {
  id: string;
  conversationId?: string;
  title: string;
  updatedAt: string;
  messages: ChatMessage[];
}

export interface AskResponse {
  reply: string;
  insights: Insight[];
  conversation_id: string;
}