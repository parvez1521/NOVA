export interface Conversation {
  id: string; title: string; created_at: string; updated_at: string; archived: boolean;
  message_count: number; last_message_preview: string | null; last_message_at: string | null;
}
export interface ChatMessage {
  id: string; conversation_id: string; role: "user" | "assistant"; content: string;
  timestamp: string; input_type: "text" | "voice"; provider: string | null; model: string | null;
  metadata: { status?: string; redacted?: boolean; memory_command?: boolean };
}
export type MemoryCategory = "preference" | "profile" | "work" | "project" | "workflow" | "technical" | "communication" | "goal";
export interface Memory {
  id: string; category: MemoryCategory; content: string; source: string; confidence: number;
  created_at: string; updated_at: string; last_used_at: string | null; is_active: boolean;
}
export interface MemorySettings {
  save_chat_history: boolean; memory_enabled: boolean; memory_auto_extraction: boolean;
  memory_min_confidence: number; memory_top_k: number;
}
export interface PersistenceStatus {
  conversation: Conversation | null; messages: ChatMessage[]; settings: MemorySettings;
  database: { available: boolean; fts5: boolean; schema_version: number; warning: string | null };
}
export interface ChatSearchResult {
  conversation: Conversation; matching_message: { id: string; content: string } | null;
  timestamp: string; preview: string;
}
