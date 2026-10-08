import { nativeCommand, nativeListen, NativeEvents } from "../services/native";
import { MicrophoneError, type MicrophoneState } from "./microphone";

export interface NativeVoiceEvent { type: string; mode?: string; provider?: string; level?: number; code?: string; audio?: string; duration_ms?: number }
type NativeRecording = { audio: Uint8Array; durationMs: number };
export class NativeMicrophoneCapture {
  state: MicrophoneState = "off";
  wakeRunning = false;
  private generation = 0;
  private cleanup: (() => void) | null = null;
  private result: NativeRecording | null = null;
  private resolve: ((value: NativeRecording | null) => void) | null = null;
  private cancelStart: (() => void) | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  constructor(private onState: (state: MicrophoneState, level: number) => void) {}
  private change(state: MicrophoneState, level = 0) { this.state = state; this.onState(state, level); }
  async start(options: { deviceId: string; autoStop: boolean; silenceMs: number; maxSeconds: number; adoptExisting?: boolean; preRollMs?: number; postRollMs?: number; autoGain?: boolean }, onAutoStop: () => void) {
    if (!["off", "error"].includes(this.state)) return false;
    const generation = ++this.generation; this.change("requesting"); this.result = null;
    this.cleanup?.();
    let ready: (() => void) | undefined, failed: ((error: Error) => void) | undefined;
    const waiting = new Promise<void>((resolve, reject) => { ready = resolve; failed = reject; });
    void waiting.catch(() => undefined);
    this.cancelStart=() => ready?.();
    try {
    const cleanup = await nativeListen<NativeVoiceEvent>(NativeEvents.voice, event => {
      if (generation !== this.generation) return;
      if (event.type === "ready" && event.mode === "capture") { this.change("listening"); ready?.(); }
      else if (event.type === "level") this.onState(this.state, event.level ?? 0);
      else if (event.type === "error") { const error = new MicrophoneError(event.code ?? "MIC_CAPTURE_FAILED", "Native microphone unavailable. Check macOS microphone permission and the selected device."); this.change("error"); failed?.(error); this.resolve?.(null); }
      else if (event.type === "timeout") { ready?.(); if (this.state === "listening") onAutoStop(); }
      else if (event.type === "audio" && event.audio) {
        const binary = atob(event.audio); this.result = { audio: Uint8Array.from(binary, value => value.charCodeAt(0)), durationMs: event.duration_ms ?? 0 };
        if (this.resolve) { this.resolve(this.result); this.resolve = null; }
        else onAutoStop();
      }
    });
    if (generation !== this.generation) { cleanup();ready?.();return false; }
    this.cleanup = cleanup;
    if (options.adoptExisting) { this.change("listening"); ready?.(); }
    else await nativeCommand("voice_command", { options: { action: this.wakeRunning ? "capture" : "start", wake: this.wakeRunning, auto_stop: options.autoStop, auto_gain: options.autoGain ?? true, device_id: options.deviceId, silence_ms: options.silenceMs, max_seconds: options.maxSeconds, pre_roll_ms: options.preRollMs ?? 350, post_roll_ms: options.postRollMs ?? 250 } });
    await waiting;
    this.cancelStart=null;
    if (generation !== this.generation) return false;
    this.timer = setTimeout(onAutoStop, options.maxSeconds * 1000); return true;
    } catch {
      if (generation !== this.generation) return false;
      this.cleanup?.(); this.cleanup = null; this.cancelStart = null;
      this.change("error");
      throw new MicrophoneError("MIC_CAPTURE_FAILED", "Native microphone unavailable. Check macOS microphone permission and retry.");
    }
  }
  async stop() {
    if (this.state === "requesting") { this.cancel(); return null; }
    if (this.state !== "listening") return null;
    clearTimeout(this.timer); this.change("processing_audio");
    let result = this.result;
    if (!result) {
      const pending = new Promise<NativeRecording | null>(resolve => { this.resolve = resolve; });
      await nativeCommand("voice_command", { options: { action: "finish" } });
      result = await Promise.race([pending, new Promise<null>(resolve => setTimeout(() => resolve(null), 1500))]);
    }
    this.cleanup?.(); this.cleanup = null; this.result = null; this.resolve = null; this.change("off"); return result;
  }
  cancel() {
    ++this.generation; this.cancelStart?.(); this.cancelStart=null;clearTimeout(this.timer); this.cleanup?.(); this.cleanup = null; this.result = null; this.resolve?.(null); this.resolve = null;
    void nativeCommand("voice_command", { options: { action: "stop" } }).catch(() => undefined); this.change("off");
  }
}
