import type { ConnectionState, HealthPayload, PetState } from "../../types/nova";
import { StatusIndicator } from "../StatusIndicator/StatusIndicator";
import { VoiceButton } from "../VoiceButton/VoiceButton";
import { ChatThread } from "../History/ChatThread";
import type { ChatMessage, Conversation } from "../../types/persistence";

interface MiniPanelProps {
  state: PetState;
  connection: ConnectionState;
  health: HealthPayload | null;
  response: string;
  busy: boolean;
  text: string;
  onTextChange: (value: string) => void;
  onSubmit: () => void;
  onMicStart: () => void;
  onMicEnd: () => void;
  voiceLabel: string;
  voiceDisabled: boolean;
  transcript: string;
  onSettings: () => void;
  onStop: () => void;
  conversation: Conversation | null;
  messages: ChatMessage[];
  onHistory: () => void;
  onNewChat: () => void;
  onEarlier: () => void;
  onMemory: () => void;
  memoryHint: string;
  onComputer: () => void;
  onConnections: () => void;
  onModels: () => void;
}

export function MiniPanel({ state, connection, health, response, busy, text, onTextChange, onSubmit, onMicStart, onMicEnd, voiceLabel, voiceDisabled, transcript, onSettings, onStop, conversation, messages, onHistory, onNewChat, onEarlier, onMemory, memoryHint, onComputer, onConnections, onModels }: MiniPanelProps) {
  const isOnline = connection === "connected";

  return (
    <section className="mini-panel" aria-label="NOVA companion panel">
      <div className="mini-panel__topline">
        <div>
          <p className="eyebrow">NOVA</p>
          <h1>Your AI companion.</h1>
        </div>
        <span className={`connection-pill connection-pill--${isOnline ? "online" : "offline"}`}>
           <span /> {isOnline ? "Online" : connection === "checking" ? "Connecting…" : connection === "auth_required" ? "Session required" : "Offline"}
        </span>
      </div>

      <StatusIndicator state={state} />
      <div className="voice-label" aria-live="polite">{voiceLabel}</div>
      <p className="mini-panel__message">
        {state === "idle" ? "Tap me or use your shortcut. The local brain is ready when you are." : state === "error" || state === "warning" ? "I hit a provider problem. The details are below." : "NOVA is working on it…"}
      </p>
      {transcript && <p className="transcript-preview"><span>Heard</span> {transcript}</p>}
      {conversation && <p className="conversation-title">{conversation.title}</p>}
      <ChatThread messages={messages} response={response} total={conversation?.message_count ?? 0} onEarlier={onEarlier} />
      {memoryHint && <p className="memory-hint" role="status">{memoryHint}</p>}

      {response && (
        <div className={`response-bubble response-bubble--${state}`} aria-live="polite">
          <span className="response-bubble__label">NOVA</span>
          <p>{response}</p>
        </div>
      )}

      <div className="mini-panel__controls">
        <VoiceButton disabled={!isOnline || voiceDisabled} listening={state === "listening"} onStart={onMicStart} onEnd={onMicEnd} />
        <form className="text-command" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
          <input value={text} onChange={(event) => onTextChange(event.target.value)} placeholder="Say something…" aria-label="Type a command" />
          <button type="submit" aria-label="Send command" disabled={!text.trim() || busy || !isOnline}>↗</button>
        </form>
        <button className="icon-button icon-button--quiet" type="button" onClick={onStop} aria-label="Stop NOVA">■</button>
      </div>

      <div className="mini-panel__footer">
        <span>{health?.capabilities.llm.configured_provider ?? "local"} brain</span>
        <button className="settings-link" type="button" onClick={onHistory}>History</button>
        <button className="settings-link" type="button" onClick={onNewChat} disabled={busy}>New Chat</button>
        <button className="settings-link" type="button" onClick={onMemory}>Memory</button>
        <button className="settings-link" type="button" onClick={onComputer}>Computer</button>
         <button className="settings-link" type="button" onClick={onConnections}>Connections</button>
         <button className="settings-link" type="button" onClick={onModels}>AI Models</button>
        <button type="button" className="settings-link" onClick={onSettings}>Voice settings</button>
      </div>
    </section>
  );
}
