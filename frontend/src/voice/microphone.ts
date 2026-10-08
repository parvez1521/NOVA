import { energy, normalizeAudio, SilenceDetector } from "./audio.ts";

export type MicrophoneState = "off" | "requesting" | "listening" | "processing_audio" | "error";

export class MicrophoneError extends Error {
  code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

export function microphoneError(error: unknown): MicrophoneError {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return new MicrophoneError("MIC_PERMISSION_DENIED", "Microphone permission is required. Allow NOVA in your browser and macOS Privacy & Security → Microphone.");
  if (name === "NotFoundError" || name === "OverconstrainedError") return new MicrophoneError("MIC_UNAVAILABLE", "No usable microphone found. Choose another input or use text.");
  return error instanceof MicrophoneError ? error : new MicrophoneError("MIC_CAPTURE_FAILED", "Microphone capture failed. Check the selected device and use text if needed.");
}

export class MicrophoneCapture {
  state: MicrophoneState = "off";
  private generation = 0;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private chunks: Float32Array[] = [];
  private maxTimer: ReturnType<typeof setTimeout> | undefined;
  private startedAt = 0;
  private onState: (state: MicrophoneState, level: number) => void;

  constructor(onState: (state: MicrophoneState, level: number) => void) { this.onState = onState; }

  private change(state: MicrophoneState, level = 0) { this.state = state; this.onState(state, level); }

  async start(options: { deviceId: string; autoStop: boolean; silenceMs: number; maxSeconds: number; adoptExisting?: boolean; preRollMs?: number; postRollMs?: number; autoGain?: boolean }, onAutoStop: () => void): Promise<boolean> {
    if (this.state !== "off" && this.state !== "error") return false;
    const generation = ++this.generation;
    this.change("requesting");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new MicrophoneError("MIC_UNAVAILABLE", "Microphone capture requires localhost or HTTPS and a supported browser.");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        deviceId: options.deviceId ? { exact: options.deviceId } : undefined,
        channelCount: 1, echoCancellation: true, noiseSuppression: true,
        autoGainControl: options.autoGain ?? true,
      } });
      if (generation !== this.generation) { stream.getTracks().forEach((track) => track.stop()); return false; }
      this.stream = stream;
      this.context = new AudioContext();
      const context = this.context;
      await context.resume();
      await context.audioWorklet.addModule("/pcm-worklet.js");
      if (generation !== this.generation) return false;
      this.node = new AudioWorkletNode(context, "nova-pcm");
      context.createMediaStreamSource(stream).connect(this.node);
      this.node.connect(context.destination);
      this.chunks = [];
      this.startedAt = performance.now();
      const vad = new SilenceDetector(options.silenceMs);
      this.node.port.onmessage = (event: MessageEvent<{ samples?: Float32Array }>) => {
        if (event.data.samples && generation === this.generation) {
          this.chunks.push(event.data.samples);
          const level = energy(event.data.samples);
          this.onState(this.state, level);
          if (options.autoStop && this.state === "listening" && vad.update(level, performance.now())) onAutoStop();
        }
      };
      this.change("listening");
      this.maxTimer = setTimeout(onAutoStop, options.maxSeconds * 1000);
      return true;
    } catch (error) {
      if (generation !== this.generation) return false;
      this.release(); this.change("error");
      throw microphoneError(error);
    }
  }

  async stop(): Promise<{ audio: Uint8Array; durationMs: number } | null> {
    if (this.state === "requesting") { this.cancel(); return null; }
    if (this.state !== "listening" || !this.context || !this.node) return null;
    clearTimeout(this.maxTimer);
    this.change("processing_audio");
    const node = this.node;
    const rate = this.context.sampleRate;
    const generation = this.generation;
    const durationMs = Math.round(performance.now() - this.startedAt);
    // End microphone access immediately; then drain the in-memory worklet buffer.
    this.stream?.getTracks().forEach((track) => track.stop());
    await new Promise<void>((resolve) => {
      const previous = node.port.onmessage;
      const timer = setTimeout(resolve, 150);
      node.port.onmessage = (event) => {
        if (event.data.done) { clearTimeout(timer); resolve(); }
        else previous?.call(node.port, event);
      };
      node.port.postMessage("stop");
    });
    const chunks = this.chunks;
    this.release(); this.chunks = [];
    try {
      if (generation !== this.generation) return null;
      if (!chunks.length || durationMs < 150) throw new MicrophoneError("INVALID_AUDIO", "Recording too short. Hold the microphone button while speaking.");
      const audio = await normalizeAudio(chunks, rate);
      if (generation !== this.generation) return null;
      this.change("off");
      return { audio, durationMs };
    } catch (error) { this.change("error"); throw microphoneError(error); }
  }

  cancel(): void { ++this.generation; this.release(); this.chunks = []; this.change("off"); }

  private release(): void {
    clearTimeout(this.maxTimer);
    this.stream?.getTracks().forEach((track) => track.stop());
    this.node?.disconnect();
    if (this.context) void this.context.close().catch(() => undefined);
    this.stream = null; this.context = null; this.node = null;
  }
}
