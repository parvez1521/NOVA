import { localFetch } from "./api";
import type { ChatMessage, ChatSearchResult, Conversation, Memory, MemorySettings, PersistenceStatus } from "../types/persistence";

export async function request<T>(path: string, method = "GET", data?: unknown): Promise<T> {
  const response = await localFetch(`/api${path}`, {
    method, headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === "string" ? error.detail : "Local history/memory request failed.");
  }
  return response.json();
}

export const persistenceApi = {
  state: () => request<PersistenceStatus>("/persistence"),
  conversations: (archived = false, offset = 0) => request<Conversation[]>(`/conversations?archived=${archived}&offset=${offset}`),
  conversation: (id: string) => request<Conversation>(`/conversations/${id}`),
  messages: (id: string, offset = 0) => request<ChatMessage[]>(`/conversations/${id}/messages?offset=${offset}`),
  create: () => request<Conversation>("/conversations", "POST", {}),
  rename: (id: string, title: string) => request<Conversation>(`/conversations/${id}`, "PATCH", { title }),
  archive: (id: string, archived = true) => request<Conversation>(`/conversations/${id}/${archived ? "archive" : "restore"}`, "POST"),
  deleteConversation: (id: string) => request(`/conversations/${id}?confirmed=true`, "DELETE"),
  search: (q: string, archived = false) => request<ChatSearchResult[]>(`/conversations/search?q=${encodeURIComponent(q)}&archived=${archived}`),
  memories: (offset = 0) => request<Memory[]>(`/memories?offset=${offset}`),
  createMemory: (content: string) => request<Memory>("/memories", "POST", { content }),
  editMemory: (id: string, content: string, category: Memory["category"]) => request<Memory>(`/memories/${id}`, "PATCH", { content, category }),
  deleteMemory: (id: string) => request(`/memories/${id}?confirmed=true`, "DELETE"),
  clear: (scope: "chats" | "memories" | "everything") => request<PersistenceStatus>("/data/clear", "POST", { scope, confirmed: true }),
  saveSettings: (settings: MemorySettings) => request<MemorySettings>("/settings/memory", "PUT", settings),
};

export async function exportData(kind: "chats" | "memories", format: "json" | "markdown") {
  const response = await localFetch(`/api/export/${kind}?format=${format}`);
  if (!response.ok) throw new Error("Local export failed.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = `nova-${kind}.${format === "json" ? "json" : "md"}`;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
