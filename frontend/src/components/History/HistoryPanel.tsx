import { useEffect, useState } from "react";
import { persistenceApi } from "../../services/persistence";
import type { ChatSearchResult, Conversation } from "../../types/persistence";

function dayGroup(timestamp: string) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
  const date = new Date(timestamp);
  return date >= today ? "Today" : date >= yesterday ? "Yesterday" : "Previous";
}

export function HistoryPanel({ conversations, onOpen, onNew, onChange, onMore, onClose }: {
  conversations: Conversation[];
  onOpen: (id: string) => Promise<void>; onNew: () => Promise<void>;
  onChange: () => Promise<void>; onClose: () => void;
  onMore: (archived: boolean) => Promise<boolean>;
}) {
  const [matches, setMatches] = useState<ChatSearchResult[]>([]);
  const [query, setQuery] = useState(""); const [archived, setArchived] = useState(false);
  const [error, setError] = useState(""); const [editing, setEditing] = useState(""); const [title, setTitle] = useState("");
  const [revision, setRevision] = useState(0);
  const [more, setMore] = useState(true);
  useEffect(() => setMore(true), [archived]);
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      const task = query.trim() ? persistenceApi.search(query, archived).then((items) => { if (!cancelled) setMatches(items); }) : Promise.resolve();
      void task.catch((error) => { if (!cancelled) setError(error.message); });
    }, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query, archived, revision]);
  async function act(action: () => Promise<unknown>) {
    try { setError(""); await action(); await onChange(); setRevision((value) => value + 1); }
    catch (error) { setError(error instanceof Error ? error.message : "History action failed."); }
  }
  const items = query.trim() ? matches.map((match) => ({ ...match.conversation, last_message_preview: match.preview, updated_at: match.timestamp })) : conversations.filter((item) => item.archived === archived);
  return <section className="voice-settings history-panel" aria-label="Chat history">
    <header><h2>History</h2><button onClick={onClose} aria-label="Close history">×</button></header>
    <div className="persistence-toolbar"><button onClick={() => { void act(onNew); }}>New Chat</button>
      <label className="voice-settings__toggle">Archived<input type="checkbox" checked={archived} onChange={(event) => setArchived(event.target.checked)} /></label></div>
    <input className="history-search" aria-label="Search chat history" placeholder="Search local chats…" maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} />
    {error && <p role="alert">{error}</p>}
    {!items.length && <p>{query ? "No matches." : "No conversations here yet."}</p>}
    {(["Today", "Yesterday", "Previous"] as const).map((group) => {
      const grouped = items.filter((item) => dayGroup(item.updated_at) === group);
      return grouped.length ? <div key={group}><h3>{group}</h3>{grouped.map((conversation, index) => <article className="history-item" key={`${conversation.id}-${index}`}>
        <button className="history-item__open" onClick={() => { void act(async () => {
          if (conversation.archived) await persistenceApi.archive(conversation.id, false);
          await onOpen(conversation.id); onClose();
        }); }} aria-label={`Open ${conversation.title}`}><strong>{conversation.title}</strong><span>{conversation.last_message_preview || "Empty conversation"}</span></button>
        <time dateTime={conversation.updated_at}>{new Date(conversation.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
        {editing === conversation.id ? <form onSubmit={(event) => { event.preventDefault(); void act(async () => { await persistenceApi.rename(conversation.id, title); setEditing(""); }); }}>
          <input aria-label="Conversation title" value={title} maxLength={60} onChange={(event) => setTitle(event.target.value)} /><button disabled={!title.trim()}>Save title</button><button type="button" onClick={() => setEditing("")}>Cancel</button>
        </form> : <div className="persistence-actions">
          <button onClick={() => { setEditing(conversation.id); setTitle(conversation.title); }}>Rename</button>
          <button onClick={() => { void act(() => persistenceApi.archive(conversation.id, !conversation.archived)); }}>{conversation.archived ? "Restore" : "Archive"}</button>
          <button onClick={() => { if (window.confirm(`Permanently delete “${conversation.title}” and its messages?`)) void act(() => persistenceApi.deleteConversation(conversation.id)); }}>Delete</button>
        </div>}
      </article>)}</div> : null;
    })}
    {!query && more && items.length >= 100 && <button onClick={() => { void onMore(archived).then(setMore).catch((error) => setError(error.message)); }}>Load more conversations</button>}
  </section>;
}
