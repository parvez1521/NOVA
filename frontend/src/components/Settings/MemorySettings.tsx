import { useState } from "react";
import { exportData, persistenceApi } from "../../services/persistence";
import type { Memory, MemoryCategory, MemorySettings as Options } from "../../types/persistence";

const groups: { title: string; categories: MemoryCategory[] }[] = [
  { title: "Preferences", categories: ["preference", "communication"] }, { title: "Profile", categories: ["profile"] },
  { title: "Work", categories: ["work"] }, { title: "Projects", categories: ["project"] },
  { title: "Workflow", categories: ["workflow"] }, { title: "Technical", categories: ["technical"] }, { title: "Goals", categories: ["goal"] },
];

export function MemorySettings({ settings, memories, onSave, onReload, onClear, onClose, onVoice }: {
  settings: Options; memories: Memory[]; onSave: (options: Options) => Promise<void>;
  onReload: () => Promise<void>; onClear: (scope: "chats" | "memories" | "everything") => Promise<void>;
  onClose: () => void; onVoice: () => void;
}) {
  const [options, setOptions] = useState(settings);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(""); const [content, setContent] = useState("");
  const [category, setCategory] = useState<MemoryCategory>("preference"); const [newMemory, setNewMemory] = useState("");
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await action(); } catch (error) { setError(error instanceof Error ? error.message : "Local memory action failed."); }
    finally { setBusy(false); }
  }
  return <section className="voice-settings memory-settings" aria-label="Memory settings">
    <header><h2>Memory & privacy</h2><button onClick={onClose} aria-label="Close memory settings">×</button></header>
    <nav className="persistence-actions"><button onClick={onVoice}>Voice</button><button disabled>Memory</button></nav>
    <p>Chats record conversations. Memories hold stable facts. Everything is saved locally in SQLite.</p>
    <form onSubmit={(event) => { event.preventDefault(); void act(() => onSave(options)); }}>
      {(["save_chat_history", "memory_enabled", "memory_auto_extraction"] as const).map((key) => <label key={key} className="voice-settings__toggle">
        <span>{{ save_chat_history: "Save chat history", memory_enabled: "Memory enabled", memory_auto_extraction: "Automatic memory extraction" }[key]}</span>
        <input type="checkbox" checked={options[key]} onChange={(event) => setOptions((current) => ({ ...current, [key]: event.target.checked }))} />
      </label>)}
      <label>Confidence threshold · {options.memory_min_confidence.toFixed(2)}<input type="range" min="0.75" max="1" step="0.05" value={options.memory_min_confidence} onChange={(event) => setOptions((current) => ({ ...current, memory_min_confidence: Number(event.target.value) }))} /></label>
      <label>Relevant memories per reply<input type="number" min="1" max="20" value={options.memory_top_k} onChange={(event) => setOptions((current) => ({ ...current, memory_top_k: Number(event.target.value) }))} /></label>
      <button disabled={busy}>Save memory settings</button>
    </form>
    <h3>Saved memories</h3>
    {!memories.length && <p>No saved memories yet. Try “Remember that I prefer Hinglish.”</p>}
    {groups.map((group) => {
      const items = memories.filter((memory) => group.categories.includes(memory.category));
      return items.length ? <div key={group.title}><h4>{group.title}</h4>{items.map((memory) => <article key={memory.id} className="memory-item">
        {editing === memory.id ? <form onSubmit={(event) => { event.preventDefault(); void act(async () => {
          await persistenceApi.editMemory(memory.id, content, category); setEditing(""); await onReload();
        }); }}><textarea aria-label="Memory content" maxLength={1000} value={content} onChange={(event) => setContent(event.target.value)} />
          <select aria-label="Memory category" value={category} onChange={(event) => setCategory(event.target.value as MemoryCategory)}>
            {groups.flatMap((group) => group.categories).map((category) => <option key={category}>{category}</option>)}
          </select><div className="persistence-actions"><button disabled={busy || !content.trim()}>Save memory</button><button type="button" onClick={() => setEditing("")}>Cancel</button></div></form>
          : <><p>{memory.content}</p><div className="persistence-actions"><button disabled={busy} onClick={() => { setEditing(memory.id); setContent(memory.content); setCategory(memory.category); }}>Edit</button>
            <button disabled={busy} onClick={() => { if (window.confirm("Permanently delete this saved memory?")) void act(async () => { await persistenceApi.deleteMemory(memory.id); await onReload(); }); }}>Delete</button></div></>}
      </article>)}</div> : null;
    })}
    <form onSubmit={(event) => { event.preventDefault(); void act(async () => { await persistenceApi.createMemory(newMemory); setNewMemory(""); await onReload(); }); }}>
      <label>Add a stable fact<input aria-label="New memory" value={newMemory} maxLength={1000} placeholder="I use Premiere Pro…" onChange={(event) => setNewMemory(event.target.value)} /></label>
      <button disabled={busy || !newMemory.trim() || !settings.memory_enabled}>Add memory</button>
    </form>
    <h3>Local export</h3><div className="persistence-actions">
      <button disabled={busy} onClick={() => { void act(() => exportData("chats", "json")); }}>Export Chat History</button>
      <button disabled={busy} onClick={() => { void act(() => exportData("memories", "json")); }}>Export Memories</button>
      <button disabled={busy} onClick={() => { void act(() => exportData("chats", "markdown")); }}>Chats · Markdown</button>
      <button disabled={busy} onClick={() => { void act(() => exportData("memories", "markdown")); }}>Memories · Markdown</button>
    </div>
    <h3>Clear local data</h3><div className="persistence-actions">
      {(["chats", "memories", "everything"] as const).map((scope) => <button key={scope} disabled={busy} onClick={() => {
        const message = scope === "everything" ? "Permanently clear all chats and all memories? App settings will be kept." : `Permanently clear all ${scope}?`;
        if (window.confirm(message)) void act(() => onClear(scope));
      }}>{scope === "everything" ? "Clear everything" : `Clear all ${scope}`}</button>)}
    </div>{error && <p role="alert">{error}</p>}
  </section>;
}
