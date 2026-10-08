export interface NativeVoiceStatus {
  microphone_permission?: number;
  speech_permission?: number;
  microphone_state?: "GRANTED" | "DENIED" | "NOT_DETERMINED" | "UNAVAILABLE";
  speech_recognition_state?: "GRANTED" | "DENIED" | "NOT_DETERMINED" | "UNAVAILABLE";
  apple_speech_available?: boolean;
}

export function microphoneLabel(
  stage: string,
  options: { voice_enabled?: boolean; stt_enabled?: boolean } | null,
  _connected: boolean,
  native: boolean,
  nativeStatus: NativeVoiceStatus | null,
): string {
  if (stage !== "MIC OFF") return stage;
  if (nativeStatus) {
    const state = nativeStatus?.microphone_state ?? (nativeStatus?.microphone_permission === 3 ? "GRANTED" : nativeStatus?.microphone_permission === 0 ? "NOT_DETERMINED" : nativeStatus?.microphone_permission === 1 || nativeStatus?.microphone_permission === 2 ? "DENIED" : undefined);
    if (state === "DENIED") return "MIC DENIED";
    if (state === "NOT_DETERMINED") return "MIC PERMISSION NEEDED";
    if (state === "UNAVAILABLE") return "MIC UNAVAILABLE";
    if (state === "GRANTED") return options?.voice_enabled && options?.stt_enabled ? "MIC READY" : "MIC DISABLED";
  }
  if (native) return "MIC CHECKING";
  if (!options) return "MIC CHECKING";
  return options.voice_enabled && options.stt_enabled ? "MIC CHECKING" : "MIC DISABLED";
}
