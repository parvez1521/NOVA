import type { ChatMessage } from "../../types/persistence";

export function ChatThread({ messages, response, total, onEarlier }: { messages: ChatMessage[]; response: string; total: number; onEarlier: () => void }) {
  const visible = messages.filter((message, index) => !(index === messages.length - 1 && message.role === "assistant" && message.content === response));
  if (!visible.length) return null;
  return <div className="chat-thread" aria-label="Conversation messages">
    {total > messages.length && <button className="settings-link" onClick={onEarlier}>Load earlier messages</button>}
    {visible.map((message) => <article key={message.id} className={`chat-thread__message chat-thread__message--${message.role}`}>
      <span>{message.role === "user" ? "You" : "NOVA"}{message.input_type === "voice" ? " · voice" : ""}</span>
      <p>{message.content}</p>{message.metadata.status === "cancelled" && <small>Cancelled</small>}
    </article>)}
  </div>;
}
