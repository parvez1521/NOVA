/** Mono PCM16 WAV, local resampling, and energy-only VAD. No speech cloud calls. */
export function encodeWav(samples: Float32Array, rate = 16000): Uint8Array {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const data = new DataView(bytes.buffer);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) bytes[offset + i] = value.charCodeAt(i);
  };
  text(0, "RIFF"); data.setUint32(4, bytes.length - 8, true); text(8, "WAVE");
  text(12, "fmt "); data.setUint32(16, 16, true); data.setUint16(20, 1, true);
  data.setUint16(22, 1, true); data.setUint32(24, rate, true); data.setUint32(28, rate * 2, true);
  data.setUint16(32, 2, true); data.setUint16(34, 16, true); text(36, "data");
  data.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    data.setInt16(44 + i * 2, sample * (sample < 0 ? 32768 : 32767), true);
  }
  return bytes;
}

export async function normalizeAudio(chunks: Float32Array[], sourceRate: number): Promise<Uint8Array> {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
  if (sourceRate === 16000) return encodeWav(samples);
  const context = new OfflineAudioContext(1, Math.max(1, Math.round(length * 16000 / sourceRate)), 16000);
  const source = context.createBufferSource();
  source.buffer = context.createBuffer(1, length, sourceRate);
  source.buffer.copyToChannel(samples, 0);
  source.connect(context.destination); source.start();
  const output = await context.startRendering();
  return encodeWav(output.getChannelData(0));
}

export function audioBase64(audio: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < audio.length; offset += 8192) {
    binary += String.fromCharCode(...audio.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

export function energy(samples: Float32Array): number {
  return Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / Math.max(1, samples.length));
}

export class SilenceDetector {
  lastSpeech: number | null = null;
  timeoutMs: number;
  threshold: number;
  constructor(timeoutMs: number, threshold = 0.015) { this.timeoutMs = timeoutMs; this.threshold = threshold; }
  update(level: number, now: number): boolean {
    if (level >= this.threshold) this.lastSpeech = now;
    return this.lastSpeech !== null && now - this.lastSpeech > this.timeoutMs;
  }
}
