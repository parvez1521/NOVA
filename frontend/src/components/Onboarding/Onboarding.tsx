import { useState } from "react";
import type { HealthPayload, VoiceStatus } from "../../types/nova";

const steps = [
  { title: "Welcome to NOVA", body: "A local-first companion for conversation, memory and carefully guarded computer work." },
  { title: "Microphone", body: "Voice input stays off until you hold the microphone or explicitly enable local Hey Nova listening." },
  { title: "Voice", body: "Whisper speech recognition and macOS speech playback run locally. Choose language and voice preferences in Voice settings." },
  { title: "Wake word", body: "Hey Nova is optional and remains off by default. Native builds use local on-device recognition only." },
  { title: "Computer access", body: "Computer Use starts in simulation mode. Real actions require your explicit settings and macOS permissions." },
  { title: "Local AI", body: "NOVA prefers your local Ollama model. Online providers are optional and never receive microphone audio." },
  { title: "Connections", body: "Google and other services are optional. Credentials stay in the backend secure store; every external action needs its own guard." },
  { title: "Ready", body: "You can revisit Voice, Computer, Connections and Memory from the companion panel at any time." },
];

export function Onboarding({ health, voice, onVoice, onComputer, onConnections, onClose }: { health: HealthPayload | null; voice: VoiceStatus | null; onVoice: () => void; onComputer: () => void; onConnections: () => void; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const step = steps[index];
  const finish = () => { localStorage.setItem("nova.onboarding.completed", "true"); onClose(); };
  return <aside className="onboarding" aria-label="NOVA first-run setup">
    <div className="onboarding__orb" aria-hidden="true">N</div>
    <p className="eyebrow">NOVA · FIRST RUN</p><h2>{step.title}</h2><p className="onboarding__body">{step.body}</p>
    <div className="onboarding__status"><span>Local brain</span><strong>{health?.llm.ollama?.available ? "Ready" : "Set up when you are ready"}</strong><span>Speech</span><strong>{voice?.stt.available ? "Whisper ready" : "Optional"}</strong></div>
    {index === 1 && <button className="onboarding__link" onClick={onVoice}>Open Voice settings</button>}
    {index === 4 && <button className="onboarding__link" onClick={onComputer}>Open Computer settings</button>}
    {index === 6 && <button className="onboarding__link" onClick={onConnections}>Open Connections</button>}
    <div className="onboarding__dots" aria-label={`Step ${index + 1} of ${steps.length}`}>{steps.map((_, item) => <span className={item === index ? "active" : item < index ? "done" : ""} key={item} />)}</div>
    <div className="persistence-actions"><button onClick={finish}>Skip setup</button>{index < steps.length - 1 ? <button className="onboarding__primary" onClick={() => setIndex(value => value + 1)}>Continue</button> : <button className="onboarding__primary" onClick={finish}>Start with NOVA</button>}</div>
  </aside>;
}
