export type PetState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "happy"
  | "excited"
  | "confused"
  | "sleeping"
  | "warning"
  | "error"
  | "working"
  | "success";

export type ConnectionState = "checking" | "connected" | "offline" | "auth_required";

export interface ConnectionDiagnostics {
  backend_url: string;
  http: { state: "checking" | "connected" | "failed"; status?: number; reason?: string };
  websocket: { state: "checking" | "connected" | "failed"; close_code?: number; reason?: string };
  retry_count: number;
}

export interface VoiceSettings {
  voice_enabled: boolean;
  stt_enabled: boolean;
  tts_enabled: boolean;
  microphone_id: string;
  stt_provider: "local_whisper";
  stt_profile: "FAST" | "BALANCED" | "ACCURATE";
  whisper_model: "tiny" | "base" | "small" | "medium";
  language: string;
  tts_provider: "macos";
  tts_voice: string;
  tts_language_mode: "auto" | "english" | "hindi" | "hinglish";
  tts_voice_english: string;
  tts_voice_hindi: string;
  tts_voice_fallback: string;
  tts_rate: number;
  shortcut: "Alt+Space" | "Alt+V";
  auto_stop: boolean;
  silence_timeout_ms: number;
  max_recording_seconds: number;
  wake_word_enabled: boolean;
  wake_provider: "auto" | "apple_speech" | "whisper_vad";
  wake_sensitivity: "LOW" | "MEDIUM" | "HIGH";
  followup_window_seconds: number;
  auto_gain: boolean;
  pre_roll_ms: number;
  post_roll_ms: number;
  stt_context: string;
}

export interface VoiceStatus {
  settings: VoiceSettings;
  stt: { available: boolean; model_available?: boolean; code?: string; provider: string };
  tts: { available: boolean; provider: string; playback: string; language_support?: Partial<Record<"en" | "hi" | "hinglish", boolean>> };
}

export type ModelPricingState = "LOCAL_FREE" | "FREE" | "PAID" | "UNKNOWN";
export interface ModelDescriptor {
  provider: string; model: string; name: string;
  availability: "AVAILABLE" | "UNAVAILABLE" | "UNKNOWN";
  pricing_state: ModelPricingState; local: boolean; last_checked?: number | null;
  pricing_source?: string; detail?: string;
  capabilities: Record<string, boolean | number | null>;
}
export interface RoutingSettings {
  mode: "LOCAL_ONLY" | "FREE_ONLY" | "BALANCED" | "BEST_AVAILABLE" | "OFFLINE";
  free_only: boolean; zero_budget: boolean;
  privacy_mode: "LOCAL_FIRST" | "STRICT_LOCAL" | "CLOUD_ALLOWED";
  jury_mode: boolean; preferred_local_model: string; preferred_cloud_model: string;
}
export interface ModelProviderActivation {
  configured: boolean;
  status: string;
  model: string;
  free_eligible: boolean;
  free_eligible_count: number;
  pricing_status: string;
  pricing_sources?: string[];
  allowlist?: string[];
  rate_limit?: { status: string; rate_limited_models: { model: string; state: string; retry_after_seconds: number }[] };
}
export interface ModelActivation {
  gemini: ModelProviderActivation;
  openrouter: ModelProviderActivation;
}

export interface HealthPayload {
  service: string;
  version: string;
  phase: number;
  status: "ok" | "degraded";
  environment: string;
  timestamp: string;
  system: {
    os: string;
    architecture: string;
  };
  llm: {
    ollama?: {
      provider: string;
      model: string;
      configured: boolean;
      available: boolean;
      server_reachable: boolean | null;
      model_available: boolean | null;
      detail?: string | null;
    };
    openrouter?: {
      provider: string;
      model: string;
      configured: boolean;
      available: boolean;
      server_reachable: boolean | null;
      model_available: boolean | null;
      detail?: string | null;
    };
    routing_policy: string;
  };
  capabilities: {
    phase: number;
    llm: {
      configured_provider: string;
      ollama_model: string;
      ollama_installed: boolean;
      ollama_reachable: boolean | null;
      openrouter_enabled: boolean;
      openrouter_configured: boolean;
    };
    voice: {
      enabled: boolean;
      native_tts: boolean;
      stt: string;
    };
    computer_use?: {
      permissions?: {
        accessibility?: boolean;
        screen_recording?: boolean;
        browser_access?: boolean;
        filesystem_access?: boolean;
        helper_available?: boolean;
      };
      permission_state?: Record<string, string>;
      settings?: Record<string, boolean | string | number>;
    };
  };
}

export interface NovaEvent {
  type: string;
  timestamp: string;
  request_id?: string;
  conversation_id?: string;
  data?: Record<string, unknown>;
  content?: string;
  provider?: string | null;
  model?: string | null;
  code?: string;
  message?: string;
  latency_ms?: number;
  text?: string;
  raw_transcript?: string;
  language?: string | null;
  duration_ms?: number;
  queue_length?: number;
  recoverable?: boolean;
  thinking_enabled?: boolean;
  thinking_reason?: string;
  ttft_ms?: number | null;
  total_generation_latency_ms?: number | null;
  token_count?: number;
  token_count_kind?: string;
  detected_answer_language?: "en" | "hi" | "hinglish";
  answer_language?: "en" | "hi" | "hinglish";
  tts_language_mode?: VoiceSettings["tts_language_mode"];
  language_uncertain?: boolean;
  language_resolution?: string;
  selected_tts_voice?: string;
  tts_language?: string;
  tts_voice_locale?: string;
  fallback_used?: boolean;
  voice_selection_reason?: string;
  original_answer_length?: number;
  cleaned_speech_length?: number;
  speech_chars_removed?: number;
  retrieved_memory_count?: number;
  retrieved_memory_ids?: string[];
  retrieval_scores?: number[];
  memory_inserted?: number;
  memory_updated?: number;
  memory_deleted?: number;
  database_latency_ms?: number;
  message_count?: number;
  action?: string;
  choices?: string[];
  task_id?: string;
  task_goal?: string;
  task_status?: import("./computer").TaskStatus;
  task_mode?: string;
  current_step?: string;
  step_index?: number;
  total_steps?: number;
  retry_count?: number;
  completed_actions?: string[];
  confirmation?: { tool: string; arguments: Record<string, unknown>; scope_extension?: boolean };
  last_action?: Record<string, unknown>;
  last_observation?: Record<string, unknown>;
  verification?: Record<string, unknown>;
  simulation?: boolean;
  task_duration_ms?: number;
  plan?: string[];
  last_result?: Record<string, unknown>;
  step_id?: string;
  summary?: string;
  metadata?: Record<string, unknown>;
  failure?: Record<string, unknown>;
  pipeline_trace?: Record<string, unknown>;
  wake_phrase_removed?: boolean;
  wake_only?: boolean;
}
