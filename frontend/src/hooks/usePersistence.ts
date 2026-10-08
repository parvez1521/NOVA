import { useCallback, useReducer, useRef } from "react";
import { persistenceApi } from "../services/persistence";
import type { NovaEvent } from "../types/nova";
import type { ChatMessage, Conversation, Memory, MemorySettings, PersistenceStatus } from "../types/persistence";

interface State {
  activeConversation: Conversation | null; conversations: Conversation[]; messages: ChatMessage[];
  memories: Memory[]; memorySettings: MemorySettings | null; database: PersistenceStatus["database"] | null;
}
type Action = { type: "loaded"; status: PersistenceStatus; conversations: Conversation[] }
  | { type: "opened"; conversation: Conversation; messages: ChatMessage[] }
  | { type: "messages"; messages: ChatMessage[] } | { type: "memories"; memories: Memory[] }
  | { type: "settings"; settings: MemorySettings } | { type: "catalog"; conversations: Conversation[] };
const initial: State = { activeConversation: null, conversations: [], messages: [], memories: [], memorySettings: null, database: null };
function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "loaded": return { ...state, activeConversation: action.status.conversation, messages: action.status.messages,
      memorySettings: action.status.settings, database: action.status.database, conversations: action.conversations };
    case "opened": return { ...state, activeConversation: action.conversation, messages: action.messages };
    case "messages": return { ...state, messages: action.messages };
    case "memories": return { ...state, memories: action.memories };
    case "settings": return { ...state, memorySettings: action.settings };
    case "catalog": return { ...state, conversations: action.conversations };
  }
}

export function usePersistence(onNotice: (message: string) => void) {
  const [state, dispatch] = useReducer(reducer, initial);
  const stateRef = useRef(state); stateRef.current = state;
  const version = useRef(0);
  const memoryVersion = useRef(0);
  const refresh = useCallback(async (id?: string) => {
    const current = ++version.current;
    try {
      const [status, active, archived] = await Promise.all([persistenceApi.state(), persistenceApi.conversations(), persistenceApi.conversations(true)]);
      const conversations = [...active, ...archived];
      if (id && status.conversation?.id !== id) {
        const conversation = await persistenceApi.conversation(id).catch(() => null);
        if (conversation && !conversation.archived) {
          status.conversation = conversation; status.messages = await persistenceApi.messages(id);
        }
      }
      if (version.current !== current) return;
      dispatch({ type: "loaded", status, conversations });
      if (!status.database.available) onNotice("Local saving is temporarily unavailable; chat still works.");
    } catch (error) { onNotice(error instanceof Error ? error.message : "History unavailable; chat still works."); }
  }, [onNotice]);
  const open = async (id: string) => {
    ++version.current;
    const [conversation, messages] = await Promise.all([persistenceApi.conversation(id), persistenceApi.messages(id)]);
    dispatch({ type: "opened", conversation, messages });
    return conversation;
  };
  const loadEarlier = async () => {
    const active = stateRef.current.activeConversation;
    if (!active) return;
    const messages = await persistenceApi.messages(active.id, stateRef.current.messages.length);
    if (stateRef.current.activeConversation?.id === active.id) dispatch({ type: "messages", messages: [...messages, ...stateRef.current.messages] });
  };
  const loadMemories = useCallback(async () => {
    const current = ++memoryVersion.current;
    const memories: Memory[] = [];
    for (let offset = 0; ; offset += 200) {
      const page = await persistenceApi.memories(offset); memories.push(...page);
      if (page.length < 200) break;
    }
    if (memoryVersion.current === current) dispatch({ type: "memories", memories });
  }, []);
  const loadMoreConversations = async (archived: boolean) => {
    const offset = stateRef.current.conversations.filter((conversation) => conversation.archived === archived).length;
    const page = await persistenceApi.conversations(archived, offset);
    const conversations = [...new Map([...stateRef.current.conversations, ...page].map((conversation) => [conversation.id, conversation])).values()];
    dispatch({ type: "catalog", conversations });
    return page.length > 0;
  };
  const onEvent = useCallback((event: NovaEvent) => {
    if (event.type === "system.ready" || event.type === "conversation.updated" || event.type === "agent.completed" || event.type === "agent.cancelled" || (event.type === "system.error" && !event.recoverable)) {
      void refresh(event.conversation_id ?? stateRef.current.activeConversation?.id).catch(() => undefined);
    }
    if (event.type === "system.ready" || event.type.startsWith("memory.")) {
      void loadMemories().catch(() => onNotice("Saved memories are temporarily unavailable."));
    }
  }, [refresh, loadMemories, onNotice]);
  return { state, dispatch, refresh, open, loadEarlier, loadMemories, loadMoreConversations, onEvent };
}
