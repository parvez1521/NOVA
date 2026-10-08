import { useEffect, useState } from "react";
import { voiceNames } from "../../services/api";
import type { VoiceSettings as Options, VoiceStatus } from "../../types/nova";
import { nativeAvailable, nativeCommand } from "../../services/native";

export function VoiceSettings({ status, onSave, onClose, onMemory }: { status: VoiceStatus; onSave: (options: Options) => Promise<void>; onClose: () => void; onMemory: () => void }) {
  const [options, setOptions] = useState(status.settings);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [voices, setVoices] = useState<{ name: string; language: string }[]>([]);
  const [nativeDevices, setNativeDevices] = useState<{id:string;name:string}[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!nativeAvailable) void navigator.mediaDevices?.enumerateDevices().then((devices) => setDevices(devices.filter((device) => device.kind === "audioinput"))).catch(() => setError("Microphone list unavailable until browser permission is granted."));
    void voiceNames().then(setVoices).catch(() => setError("Native voice list unavailable."));
    if (nativeAvailable) void nativeCommand<{devices:{id:string;name:string}[]}>("voice_status").then(result => setNativeDevices(result.devices)).catch(() => setError("Native microphone list unavailable."));
  }, []);
  const update = <K extends keyof Options>(key: K, value: Options[K]) => setOptions((current) => ({ ...current, [key]: value }));
  const voiceOptions = (language: "en" | "hi", selected: string) => {
    const installed = voices.filter((voice) => voice.language.split(/[_-]/)[0] === language);
    return <>
      <option value="">Discover a suitable installed voice</option>
      {selected && !installed.some((voice) => voice.name === selected) && <option value={selected}>{selected} · unavailable or unsuitable (will fall back)</option>}
      {installed.map((voice) => <option key={voice.name} value={voice.name}>{voice.name} · {voice.language}</option>)}
    </>;
  };
  return <section className="voice-settings" aria-label="Voice settings">
    <header><h2>Voice</h2><button type="button" onClick={onClose} aria-label="Close voice settings">×</button></header>
    <nav className="persistence-actions"><button disabled>Voice</button><button onClick={onMemory}>Memory</button></nav>
    <p>Local input. Local speech. Wake listening stays off until you enable it.</p>
    <form onSubmit={async (event) => {
      event.preventDefault(); setSaving(true); setError("");
      try { await onSave(options); onClose(); } catch (error) { setError(error instanceof Error ? error.message : "Save failed"); }
      finally { setSaving(false); }
    }}>
      {(["voice_enabled", "stt_enabled", "tts_enabled", "auto_stop"] as const).map((key) => <label key={key} className="voice-settings__toggle">
        <span>{{ voice_enabled: "Voice enabled", stt_enabled: "Speech input", tts_enabled: "Spoken responses", auto_stop: "Auto-stop after silence" }[key]}</span>
        <input type="checkbox" checked={options[key]} onChange={(event) => update(key, event.target.checked)} />
      </label>)}
      <label>Microphone<select value={options.microphone_id} onChange={(event) => update("microphone_id", event.target.value)}>
        <option value="">System default</option>{devices.filter((device) => device.deviceId).map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Microphone ${index + 1}`}</option>)}
        {nativeDevices.map(device => <option key={device.id} value={device.id}>{device.name}</option>)}
      </select></label>
      {nativeAvailable && <><label className="voice-settings__toggle">Hey Nova · local wake listening<input type="checkbox" checked={options.wake_word_enabled} onChange={event => update("wake_word_enabled", event.target.checked)} /></label>
        <label>Wake engine<select value={options.wake_provider} onChange={event => update("wake_provider", event.target.value as Options["wake_provider"])}><option value="auto">Discover an available local engine</option><option value="apple_speech">macOS on-device speech</option><option value="whisper_vad">VAD + installed tiny/base Whisper</option></select></label>
        <label>Wake sensitivity<select value={options.wake_sensitivity} onChange={event => update("wake_sensitivity", event.target.value as Options["wake_sensitivity"])}><option>LOW</option><option>MEDIUM</option><option>HIGH</option></select></label>
        <label>Follow-up window · {options.followup_window_seconds} seconds<input type="range" min="0" max="30" value={options.followup_window_seconds} onChange={event => update("followup_window_seconds", Number(event.target.value))} /></label>
        <p>Waiting audio stays in bounded RAM buffers. Only VAD speech windows reach an eligible local keyword recognizer. No cloud recognition or saved passive transcripts.</p></>}
      <label>STT provider<input readOnly value="Local Whisper · whisper.cpp" /></label>
       <label>STT profile<select value={options.stt_profile} onChange={(event) => {
         const profile = event.target.value as Options["stt_profile"];
         const model = { FAST: "base", BALANCED: "small", ACCURATE: "medium" }[profile] as Options["whisper_model"];
         setOptions(current => ({ ...current, stt_profile: profile, whisper_model: model }));
       }}>
         <option value="FAST">FAST · base · lowest latency</option><option value="BALANCED">BALANCED · small · recommended</option><option value="ACCURATE">ACCURATE · medium · best recognition</option>
       </select></label>
       <p>Profiles select the bundled local Whisper model. No model is downloaded automatically; the current model is <strong>{options.whisper_model}</strong>.</p>
      <label className="voice-settings__toggle">Automatic gain / local conditioning<input type="checkbox" checked={options.auto_gain} onChange={event => update("auto_gain",event.target.checked)} /></label>
      <label>Pre-roll · {options.pre_roll_ms} ms<input type="range" min="0" max="1000" step="50" value={options.pre_roll_ms} onChange={event => update("pre_roll_ms",Number(event.target.value))} /></label>
      <label>Post-roll · {options.post_roll_ms} ms<input type="range" min="0" max="1000" step="50" value={options.post_roll_ms} onChange={event => update("post_roll_ms",Number(event.target.value))} /></label>
      <label>Microphone language (STT)<select value={options.language} onChange={(event) => update("language", event.target.value)}>
        <option value="auto">Detect automatically (English / Hindi / Hinglish)</option><option value="en">English</option><option value="hi">Hindi / Hinglish</option>
      </select></label>
      <label>Recognition vocabulary<input value={options.stt_context} onChange={event => update("stt_context", event.target.value)} maxLength={500} /></label>
      <label>TTS provider<input readOnly value="macOS say · Mac audio output" /></label>
      <label>Spoken answer language<select value={options.tts_language_mode} onChange={(event) => update("tts_language_mode", event.target.value as Options["tts_language_mode"])}>
        <option value="auto">Detect from the answer</option><option value="english">English</option><option value="hindi">Hindi</option><option value="hinglish">Hinglish (English voice)</option>
      </select></label>
      <label>English / Hinglish voice<select value={options.tts_voice_english} onChange={(event) => update("tts_voice_english", event.target.value)}>
        {voiceOptions("en", options.tts_voice_english)}
      </select></label>
      <label>Hindi voice<select value={options.tts_voice_hindi} onChange={(event) => update("tts_voice_hindi", event.target.value)}>
        {voiceOptions("hi", options.tts_voice_hindi)}
      </select></label>
      <label>Safe fallback voice<select value={options.tts_voice_fallback} onChange={(event) => update("tts_voice_fallback", event.target.value)}>
        {voiceOptions("en", options.tts_voice_fallback)}
      </select></label>
      <p>Hindi uses an installed Hindi voice, then a safe English fallback. Hinglish prefers Indian English; choose Hindi above to use a Hindi voice for romanized text.</p>
      <label>Speech rate · {options.tts_rate}<input type="range" min="100" max="350" value={options.tts_rate} onChange={(event) => update("tts_rate", Number(event.target.value))} /></label>
      <label>Shortcut (NOVA focused)<select value={options.shortcut} onChange={(event) => update("shortcut", event.target.value as Options["shortcut"])}><option value="Alt+Space">Option + Space</option><option value="Alt+V">Option + V</option></select></label>
      <label>Silence timeout · {options.silence_timeout_ms} ms<input type="range" min="500" max="5000" step="100" value={options.silence_timeout_ms} onChange={(event) => update("silence_timeout_ms", Number(event.target.value))} /></label>
      <p className="voice-settings__availability">STT: {status.stt.available ? "ready" : status.stt.code ?? "unavailable"} · TTS: {status.tts.available ? "ready" : "unavailable"}. Models are never downloaded by changing settings.</p>
      {error && <p role="alert">{error}</p>}<button type="submit" disabled={saving}>{saving ? "Saving…" : "Save voice settings"}</button>
    </form>
  </section>;
}
