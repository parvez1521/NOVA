import { nativeCommand, nativeListen, NativeEvents } from "../services/native";
import { localFetch } from "../services/api";
import type { VoiceSettings } from "../types/nova";
import type { NativeVoiceEvent } from "./nativeMicrophone";

export type WakeState = "OFF" | "STARTING" | "PASSIVE" | "LISTENING" | "FOLLOWUP" | "UNAVAILABLE";
export interface WakeWordProvider { start(): Promise<void>; stop(): Promise<void>; is_available(): Promise<boolean>; on_wake(callback: () => void): void }

// React reconnects can replace a provider before its async cleanup finishes.
// Serialize access to the one native voice engine across provider instances.
let lifecycle: Promise<unknown> = Promise.resolve();
function serialize<T>(operation: () => Promise<T>): Promise<T> {
  const next = lifecycle.then(operation);
  lifecycle = next.catch(() => undefined);
  return next;
}
const unavailableMessage = "Wake word unavailable. Check the local wake engine and macOS microphone/speech permissions, then retry.";

export class LocalWakeProvider implements WakeWordProvider {
  private callback: (() => void) | null = null;
  private cleanup: (() => void) | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private provider = "apple_speech";
  private enabled = false;
  private state: WakeState = "OFF";
  private generation = 0;
  private starting: Promise<void> | null = null;
  private cancelReady: (() => void) | null = null;
  private engineStarted = false;
  private keywordStarted = false;
  constructor(private options: VoiceSettings, private onState: (state: WakeState) => void, private onError: (message: string) => void) {}
  on_wake(callback: () => void) { this.callback = callback; }
  private change(state: WakeState) { this.state = state; this.onState(state); }
  async is_available() {
     const native = await nativeCommand<{ apple_speech_available: boolean; speech_recognition_state?: string }>("voice_status");
    const response = await localFetch("/api/voice/wake/providers");
    const providers = await response.json() as { name: string; available: boolean }[];
    this.provider = this.options.wake_provider === "auto" ? native.apple_speech_available ? "apple_speech" : "whisper_vad" : this.options.wake_provider;
     return this.provider === "apple_speech"
       ? native.apple_speech_available && (native.speech_recognition_state === undefined || ["GRANTED", "NOT_DETERMINED"].includes(native.speech_recognition_state))
       : providers.some(provider => provider.name === "whisper_vad" && provider.available);
  }
  start(): Promise<void> {
    if (this.starting) return this.starting;
    if (this.enabled) return Promise.resolve();
    const generation = ++this.generation;
    this.change("STARTING");
    const starting = serialize(() => this.initialize(generation)).finally(() => {
      if (this.starting === starting) this.starting = null;
    });
    this.starting = starting;
    return starting;
  }
  private async initialize(generation: number) {
    const current = () => generation === this.generation;
    let readyTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      if (!current()) return;
      if (!await this.is_available()) throw new Error(unavailableMessage);
      if (!current()) return;
      let ready!: () => void, failed!: () => void;
      let initialized = false;
      const waiting = new Promise<void>((resolve, reject) => {
        ready = resolve;
        failed = () => reject(new Error(unavailableMessage));
      });
      // A native error can arrive while the start command is still pending.
      void waiting.catch(() => undefined);
      this.cancelReady = ready;
      const cleanup = await nativeListen<NativeVoiceEvent>(NativeEvents.voice, event => {
        if (!current() || !this.enabled) return;
        if (event.type === "ready" && event.mode === "passive") {
          initialized = true; clearTimeout(readyTimer); this.change("PASSIVE"); ready();
        } else if (!initialized && event.type === "error") failed();
        else if ((initialized && event.type === "error") || event.type === "stopped") this.unavailable(generation);
        else if (!initialized) return;
        else if (event.type === "ready" && event.mode === "followup") this.change("FOLLOWUP");
        else if (event.type === "wake.detected" || event.type === "speech.started" && event.mode === "followup") {
          clearTimeout(this.timer); this.change("LISTENING"); this.callback?.();
        } else if (event.type === "keyword.audio" && event.audio && this.provider === "whisper_vad") {
          void localFetch("/api/voice/wake/whisper_vad/inspect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ audio: event.audio }) })
            .then(response => response.json()).then(result => {
              if (current() && this.enabled && result.matched) return nativeCommand("voice_command", { options: { action: "wake" } });
            }).catch(() => { if (current()) this.unavailable(generation); });
        } else if (event.type === "timeout") this.passive();
      });
      if (!current()) { await cleanup(); return; }
      this.cleanup = cleanup;
      this.enabled = true;
      if (this.provider === "whisper_vad") {
        this.keywordStarted = true;
        const response = await localFetch("/api/voice/wake/whisper_vad/start", { method: "POST" });
        if (!response.ok) throw new Error(unavailableMessage);
      }
      if (!current()) return;
      readyTimer = setTimeout(failed, 30_000);
      this.engineStarted = true;
      await Promise.all([
        nativeCommand("voice_command", { options: { action: "start", wake: true, provider: this.provider, sensitivity: this.options.wake_sensitivity, auto_stop: true, device_id: this.options.microphone_id, silence_ms: this.options.silence_timeout_ms, pre_roll_ms: this.options.pre_roll_ms, post_roll_ms: this.options.post_roll_ms, max_seconds: this.options.max_recording_seconds } }),
        waiting,
      ]);
    } catch {
      if (!current()) return;
      this.enabled = false;
      await this.release();
      if (current()) { this.change("UNAVAILABLE"); this.onError(unavailableMessage); }
      throw new Error(unavailableMessage);
    } finally {
      clearTimeout(readyTimer);
      this.cancelReady = null;
    }
  }
  private unavailable(generation: number) {
    if (generation !== this.generation) return;
    ++this.generation; this.enabled = false; this.cancelReady?.();
    clearTimeout(this.timer);
    this.change("UNAVAILABLE"); this.onError(unavailableMessage);
    void serialize(() => this.release());
  }
  followup(speaking = false) {
    if (!this.enabled) return;
    clearTimeout(this.timer);
    const generation = this.generation;
    void nativeCommand("voice_command", { options: { action: "followup" } }).catch(() => this.unavailable(generation));
    this.timer = setTimeout(() => this.passive(), (speaking ? 60 : this.options.followup_window_seconds) * 1000);
  }
  passive() {
    if (!this.enabled) return;
    clearTimeout(this.timer);
    const generation = this.generation;
    void nativeCommand("voice_command", { options: { action: "passive" } }).catch(() => this.unavailable(generation));
  }
  stop(): Promise<void> {
    ++this.generation; this.enabled = false; this.starting = null;
    this.cancelReady?.(); clearTimeout(this.timer);
    this.change("OFF");
    return serialize(() => this.release());
  }
  private async release() {
    const cleanup = this.cleanup; this.cleanup = null;
    await cleanup?.();
    if (this.engineStarted) {
      this.engineStarted = false;
      await nativeCommand("voice_command", { options: { action: "stop" } }).catch(() => undefined);
    }
    if (this.keywordStarted) {
      this.keywordStarted = false;
      await localFetch("/api/voice/wake/whisper_vad/stop", { method: "POST" }).catch(() => undefined);
    }
  }
}
