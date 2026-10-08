import type { CSSProperties } from "react";

export function VoiceWaveform({ listening, speaking, level }: { listening: boolean; speaking: boolean; level: number }) {
  if (!listening && !speaking) return null;
  return <div className={`voice-waveform ${speaking ? "voice-waveform--speaking" : ""}`} aria-hidden="true" style={{ "--level": Math.min(1, level * 8) } as CSSProperties}>
    {Array.from({ length: 9 }, (_, index) => <span key={index} style={{ "--index": index } as CSSProperties} />)}
  </div>;
}
