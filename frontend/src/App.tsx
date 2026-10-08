import { useCallback, useEffect, useRef, useState } from "react";

import { MiniPanel } from "./components/MiniPanel/MiniPanel";
import { Pet } from "./components/Pet/Pet";
import { useNovaConnection } from "./hooks/useNovaConnection";
import type { NovaEvent, PetState, VoiceSettings as VoiceOptions, VoiceStatus } from "./types/nova";
import { MicrophoneCapture, microphoneError, type MicrophoneState } from "./voice/microphone";
import { audioBase64 } from "./voice/audio";
import { voiceStatus } from "./services/api";
import { VoiceWaveform } from "./components/VoiceWaveform/VoiceWaveform";
import { VoiceSettings } from "./components/Settings/VoiceSettings";
import { ModelSettings } from "./components/Settings/ModelSettings";
import { VoiceDebug } from "./components/Settings/VoiceDebug";
import { usePersistence } from "./hooks/usePersistence";
import { HistoryPanel } from "./components/History/HistoryPanel";
import { persistenceApi } from "./services/persistence";
import { MemorySettings } from "./components/Settings/MemorySettings";
import type { MemorySettings as MemoryOptions } from "./types/persistence";
import { TaskProgress } from "./components/Computer/TaskProgress";
import { ComputerSettings } from "./components/Settings/ComputerSettings";
import type { TaskProgress as Progress, ComputerSettings as ComputerOptions } from "./types/computer";
import { computerApi } from "./services/computer";
import { nativeAvailable, nativeCommand, nativeListen, NativeEvents, nativePermissionDiagnostics, nativeWindowLabel } from "./services/native";
import type { NativeEventName } from "./services/nativeEvents";
import { DesktopSettings } from "./components/Settings/DesktopSettings";
import { isPermissionGranted, sendNotification } from "@tauri-apps/plugin-notification";
import { NativeMicrophoneCapture } from "./voice/nativeMicrophone";
import { LocalWakeProvider, type WakeState } from "./voice/wake";
import { microphoneLabel, type NativeVoiceStatus } from "./voice/status";
import { ConnectionHub } from "./components/Connections/ConnectionHub";
import { Onboarding } from "./components/Onboarding/Onboarding";
import { getCurrentWindow } from "@tauri-apps/api/window";

function App() {
  const companionWindow = nativeAvailable && nativeWindowLabel === "companion";
  const [petState, setPetState] = useState<PetState>("idle");
  const [panelOpen, setPanelOpen] = useState(!nativeAvailable || !companionWindow);
  const [text, setText] = useState("");
  const [response, setResponse] = useState("");
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const requestRef = useRef<string | null>(null);
  const stoppingRef = useRef(false);
  const idleTimer = useRef<number | undefined>(undefined);
  const [voice, setVoice] = useState<VoiceStatus | null>(null);
  const [nativeVoiceStatus, setNativeVoiceStatus] = useState<NativeVoiceStatus | null>(null);
  const [micState, setMicState] = useState<MicrophoneState>("off");
  const [level, setLevel] = useState(0);
  const [voiceStage, setVoiceStage] = useState("MIC OFF");
  const [wakeState, setWakeState] = useState<WakeState>("OFF");
  const wakeRef = useRef<LocalWakeProvider | null>(null);
  const [transcript, setTranscript] = useState("");
  const [notice, setNotice] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [computerOpen, setComputerOpen] = useState(false);
  const [desktopOpen, setDesktopOpen] = useState(false);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(() => !nativeAvailable && localStorage.getItem("nova.onboarding.completed") !== "true");
  const [nativeMode, setNativeMode] = useState(companionWindow ? "pet" : "control");
  const [companionMenuOpen, setCompanionMenuOpen] = useState(false);
  const [controlMaximized, setControlMaximized] = useState(false);
  const [computerOptions, setComputerOptions] = useState<ComputerOptions | null>(null);
  const [taskProgress, setTaskProgress] = useState<Progress | null>(null);
  const taskRef = useRef<Progress | null>(null); taskRef.current = taskProgress;
  const taskPetRef = useRef<PetState | null>(null);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memoryHint, setMemoryHint] = useState("");
  const [memoryPrompt, setMemoryPrompt] = useState<{ action: "clear" | "choose"; choices?: string[] } | null>(null);
  useEffect(() => { if (!memoryHint) return; const timer = window.setTimeout(() => setMemoryHint(""), 3500); return () => window.clearTimeout(timer); }, [memoryHint]);
  useEffect(() => { if (nativeAvailable) void nativeCommand<NativeVoiceStatus>("voice_status").then(setNativeVoiceStatus).catch(() => setNativeVoiceStatus(null)); }, []);
  useEffect(() => {
    if (nativeAvailable || !navigator.permissions?.query) return;
    let active = true;
    let permission: PermissionStatus | undefined;
    const update = (state: PermissionState) => { if (active) setNativeVoiceStatus({ microphone_state: state === "granted" ? "GRANTED" : state === "denied" ? "DENIED" : "NOT_DETERMINED" }); };
    void navigator.permissions.query({ name: "microphone" as PermissionName }).then(result => { permission = result; update(result.state); result.onchange = () => update(result.state); }).catch(() => undefined);
    return () => { active = false; if (permission) permission.onchange = null; };
  }, []);
  useEffect(() => {
    if (!nativeAvailable || companionWindow) return;
    void getCurrentWindow().isMaximized().then(setControlMaximized).catch(() => undefined);
  }, [companionWindow]);
  const [debugOpen, setDebugOpen] = useState(false);
  const [diagnostics, setDiagnostics] = useState<Record<string, unknown>>({});
  const speechRequested = useRef(false);
  const ttsPlaying = useRef(false);
  const captureRef = useRef<MicrophoneCapture | NativeMicrophoneCapture | null>(null);
  const persistence = usePersistence(setNotice);
  const conversationRef = useRef<string | undefined>(undefined);
  conversationRef.current = persistence.state.activeConversation?.id;
  const persistenceEvents = useRef(persistence.onEvent); persistenceEvents.current = persistence.onEvent;
  const displayedConversation = useRef<string | undefined>(undefined);
  useEffect(() => {
    const id = persistence.state.activeConversation?.id;
    if (id && id !== displayedConversation.current && !requestRef.current) {
      displayedConversation.current = id;
      setResponse([...persistence.state.messages].reverse().find((message) => message.role === "assistant")?.content ?? "");
      setTranscript("");
    }
  }, [persistence.state.activeConversation?.id, persistence.state.messages]);
  if (!captureRef.current) {
    const update = (state: MicrophoneState, level: number) => { setMicState(state); setLevel(level); };
    captureRef.current = nativeAvailable ? new NativeMicrophoneCapture(update) : new MicrophoneCapture(update);
  }

  const onEvent = useCallback((event: NovaEvent) => {
    if (event.type.startsWith("task.") && event.task_id && event.task_status) {
      const status = event.task_status;
      const mapped: PetState = status === "PLANNING" ? "thinking" : status === "PAUSED" ? "sleeping" : status === "WAITING_FOR_CONFIRMATION" ? "warning" : status === "COMPLETED" ? "success" : status === "FAILED" ? "error" : status === "CANCELLED" ? "idle" : "working";
      taskPetRef.current = ["COMPLETED", "FAILED", "CANCELLED"].includes(status) ? null : mapped;
      if (!requestRef.current || !event.request_id || event.request_id === requestRef.current) setPetState(mapped);
      setTaskProgress((current) => ({ id: event.task_id!, goal: event.task_goal ?? current?.goal ?? "", status, mode: event.task_mode ?? "SEMI_AUTONOMOUS",
         currentStep: event.current_step ?? "Working…", stepIndex: event.step_index ?? 0, totalSteps: event.total_steps ?? 0,
         completed: event.completed_actions ?? current?.completed ?? [], simulation: event.simulation ?? current?.simulation ?? true,
         plan: event.plan ?? current?.plan ?? [],
        confirmation: status === "WAITING_FOR_CONFIRMATION" ? event.confirmation ?? current?.confirmation : undefined,
        timeline: event.type !== "task.updated" && event.summary ? [...(current?.id === event.task_id ? current?.timeline ?? [] : []), { type: event.type, stepId: event.step_id ?? "", timestamp: event.timestamp, summary: event.summary }].slice(-160) : current?.id === event.task_id ? current?.timeline ?? [] : [] }));
      setDiagnostics((current) => ({ ...current, task_id: event.task_id, task_goal: event.task_goal, task_mode: event.task_mode,
        current_step: event.current_step, last_action: JSON.stringify(event.last_action), retry_count: event.retry_count,
        verification_result: JSON.stringify(event.verification), active_application: event.last_observation?.active_app,
        current_url: event.last_observation?.current_url, observation_timestamp: event.last_observation?.timestamp,
         vision_provider: event.last_observation?.source, last_result: JSON.stringify(event.last_result), task_duration_ms: event.task_duration_ms,
         pipeline_trace: event.pipeline_trace, failure: event.failure,
         observation_age_ms: event.last_observation?.timestamp ? Date.now() - Date.parse(String(event.last_observation.timestamp)) : null }));
      if (status === "FAILED" && event.summary) setResponse(event.summary);
      if (["COMPLETED", "FAILED", "CANCELLED"].includes(status)) {
        persistenceEvents.current({ ...event, type: "agent.completed" });
      }
      if (nativeAvailable && ["COMPLETED", "FAILED", "WAITING_FOR_CONFIRMATION"].includes(status)) {
        void isPermissionGranted().then(granted => { if (granted) sendNotification({ title: "NOVA", body: status === "COMPLETED" ? "Task completed." : status === "FAILED" ? "Task stopped with an error." : "Your approval is needed." }); });
      }
      return;
    }
    if (event.type === "agent.completed" && event.model === "task-control" && event.request_id !== requestRef.current) {
      persistenceEvents.current(event); setNotice(event.content ?? "Task updated."); return;
    }
    const currentTaskCompletion = event.type === "agent.completed" && Boolean(event.task_id && event.task_id === taskRef.current?.id);
    if (currentTaskCompletion && requestRef.current && event.request_id !== requestRef.current) {
      persistenceEvents.current(event); if (event.content) setResponse(event.content); return;
    }
    if (event.request_id && event.request_id !== requestRef.current && !currentTaskCompletion) return;
    if (!requestRef.current && event.type.startsWith("llm.")) return;
    if (stoppingRef.current && !["tts.cancelled", "agent.cancelled", "agent.completed", "system.error"].includes(event.type)) return;
    persistenceEvents.current(event);

    const finish = (state: PetState, delay = 0) => {
      window.clearTimeout(idleTimer.current);
      requestRef.current = null;
      stoppingRef.current = false;
      ttsPlaying.current = false;
      setActiveRequestId(null);
      setPetState(state);
      setVoiceStage("MIC OFF");
      if (delay) idleTimer.current = window.setTimeout(() => setPetState("idle"), delay);
    };

    switch (event.type) {
      case "voice.listening":
        setVoiceStage("MIC PERMISSION / STARTING");
        break;
      case "voice.recording":
        setVoiceStage("MIC LISTENING"); setPetState("listening");
        break;
      case "voice.processing":
        setVoiceStage("TRANSCRIBING LOCALLY"); setPetState("thinking");
        break;
      case "voice.transcript":
        setTranscript(event.text ?? ""); setVoiceStage("MIC OFF · THINKING"); setPetState("thinking");
        setDiagnostics((current) => ({ ...current, transcript: event.raw_transcript, normalized_command: event.text, wake_phrase_removed: event.wake_phrase_removed, stt_language: event.language, audio_duration_ms: event.duration_ms, stt_latency_ms: event.latency_ms, stt_provider: event.provider }));
        break;
      case "voice.wake_only":
        setVoiceStage("MIC OFF"); setPetState("idle"); setNotice("");
        setDiagnostics((current) => ({ ...current, wake_only: true, pipeline_trace: event.pipeline_trace }));
        break;
      case "agent.started":
      case "agent.thinking":
      case "llm.started":
        if (!ttsPlaying.current) setPetState("thinking");
        if (event.type === "llm.started") setDiagnostics((current) => ({
          ...current, llm_provider: event.provider, llm_model: event.model,
          thinking_enabled: event.thinking_enabled, thinking_policy: event.thinking_reason,
          ttft_ms: "pending", total_generation_latency_ms: "pending", token_count: 0,
          token_count_kind: "visible_content_chunks",
          conversation_id: event.conversation_id, retrieved_memory_count: event.retrieved_memory_count,
          retrieved_memory_ids: event.retrieved_memory_ids, retrieval_scores: event.retrieval_scores,
          memory_inserted: event.memory_inserted, memory_updated: event.memory_updated, memory_deleted: event.memory_deleted,
          database_latency_ms: event.database_latency_ms,
        }));
        break;
      case "llm.token":
        if (!speechRequested.current || ttsPlaying.current) setPetState("speaking");
        if (event.content) setResponse((current) => current + event.content);
        break;
      case "llm.completed":
        if (!speechRequested.current || ttsPlaying.current) setPetState("speaking");
        if (event.content) setResponse(event.content);
        setDiagnostics((current) => ({
          ...current, thinking_enabled: event.thinking_enabled,
          ttft_ms: event.ttft_ms, total_generation_latency_ms: event.total_generation_latency_ms,
          token_count: event.token_count, token_count_kind: event.token_count_kind,
        }));
        break;
      case "agent.completed": {
        if (event.content && event.task_id) setResponse(event.content);
        if (event.task_id) setDiagnostics((current) => ({ ...current, task_id: event.task_id, pipeline_trace: event.pipeline_trace, failure: event.failure, provider: event.provider, model: event.model }));
        finish(stoppingRef.current || speechRequested.current ? "idle" : "speaking", speechRequested.current ? 0 : 1100);
        if (event.task_id) { window.clearTimeout(idleTimer.current); setPetState(taskRef.current?.status === "FAILED" ? "error" : "success"); }
        if (taskPetRef.current) setPetState(taskPetRef.current);
        wakeRef.current?.followup();
        break;
      }
      case "memory.created":
      case "memory.updated":
        setMemoryHint("Memory updated");
        break;
      case "memory.deleted":
        setMemoryHint("Memory removed");
        break;
      case "memory.confirmation":
        setMemoryPrompt({ action: "clear" });
        break;
      case "memory.choices":
        setMemoryPrompt({ action: "choose", choices: event.choices });
        break;
      case "tts.started":
        wakeRef.current?.followup(true);
        ttsPlaying.current = true; setPetState("speaking"); setVoiceStage("MIC OFF · SPEAKING");
        setDiagnostics((current) => ({ ...current, tts_provider: event.provider }));
        break;
      case "tts.sentence":
        ttsPlaying.current = true; setPetState("speaking"); setVoiceStage("MIC OFF · SPEAKING");
        setDiagnostics((current) => ({ ...current, speech_queue_length: event.queue_length,
          detected_answer_language: event.detected_answer_language, answer_language: event.answer_language,
          language_resolution_scope: "cleaned speech segment", language_uncertain: event.language_uncertain,
          language_resolution: event.language_resolution, tts_language_mode: event.tts_language_mode,
          selected_tts_voice: event.selected_tts_voice, tts_language: event.tts_language, tts_voice_locale: event.tts_voice_locale,
          fallback_used: event.fallback_used, voice_selection_reason: event.voice_selection_reason,
          original_answer_length: event.original_answer_length, cleaned_speech_length: event.cleaned_speech_length,
          speech_chars_removed: event.speech_chars_removed,
        }));
        break;
      case "tts.cleanup":
        setDiagnostics((current) => ({ ...current,
          original_answer_length: event.original_answer_length, cleaned_speech_length: event.cleaned_speech_length,
          speech_chars_removed: event.speech_chars_removed,
        }));
        break;
      case "tts.sentence_completed":
        ttsPlaying.current = false;
        setPetState(taskPetRef.current ?? "thinking");
        setDiagnostics((current) => ({ ...current, speech_queue_length: event.queue_length, tts_latency_ms: event.latency_ms }));
        break;
      case "tts.completed":
      case "tts.cancelled":
        ttsPlaying.current = false; setPetState("idle"); setVoiceStage("MIC OFF");
        if (taskPetRef.current) setPetState(taskPetRef.current);
        if (!stoppingRef.current) wakeRef.current?.followup();
        break;
      case "agent.cancelled":
        finish("idle");
        if (taskPetRef.current) setPetState(taskPetRef.current);
        break;
      case "system.error":
        if (event.recoverable) { ttsPlaying.current = false; setNotice(event.message ?? "Speech unavailable; continuing silently."); break; }
        captureRef.current?.cancel();
        finish(event.code === "NO_LLM_PROVIDER" || event.code === "LLM_PROVIDER_UNAVAILABLE" || event.code === "LLM_MODEL_NOT_FOUND" ? "warning" : "error", 1800);
        setResponse(event.message ?? "NOVA hit an unexpected error.");
        break;
      default:
        break;
    }
  }, []);

  const { connection, health, diagnostics: connectionDiagnostics, send } = useNovaConnection(onEvent);
  useEffect(() => { setDiagnostics((current) => ({ ...current, connection: connectionDiagnostics })); }, [connectionDiagnostics]);
  useEffect(() => { if (nativeAvailable && companionWindow) void nativeCommand("frontend_status", { connected: connection === "connected", microphoneAvailable: Boolean(navigator.mediaDevices?.getUserMedia), speechReady: Boolean(voice?.stt.available), wakeState }).catch(() => undefined); }, [connection, voice?.stt.available, wakeState, companionWindow]);
  useEffect(() => { if (nativeAvailable && companionWindow) void nativeCommand("interaction_active", { active: Boolean(activeRequestId || taskPetRef.current || !["OFF", "UNAVAILABLE"].includes(wakeState)) }).catch(() => setNotice("Global Escape unavailable; use Stop.")); }, [activeRequestId, taskProgress?.status, wakeState, companionWindow]);

  useEffect(() => () => { window.clearTimeout(idleTimer.current); captureRef.current?.cancel(); }, []);

  useEffect(() => {
    if (!debugOpen || connection !== "connected") return;
    void computerApi.permissions().then((permissions) => setDiagnostics((current) => ({ ...current, computer_permissions: JSON.stringify(permissions) }))).catch(() => undefined);
    if (nativeAvailable) void nativePermissionDiagnostics().then((permissions) => setDiagnostics((current) => ({ ...current, permission_diagnostics: JSON.stringify(permissions) }))).catch(() => undefined);
  }, [debugOpen, connection]);

  useEffect(() => {
    if (connection !== "connected") return;
    let cancelled = false;
    void computerApi.settings().then(setComputerOptions).catch(() => undefined);
    void (async () => {
      let result = await voiceStatus();
      const saved = localStorage.getItem("nova.voice.settings");
      if (saved) result = await voiceStatus({ ...result.settings, ...JSON.parse(saved) });
      if (!cancelled) setVoice(result);
      if (nativeAvailable) void nativeCommand<NativeVoiceStatus>("voice_status").then(setNativeVoiceStatus).catch(() => setNativeVoiceStatus(null));
    })().catch(() => { if (!cancelled) setNotice("Voice settings unavailable; text still works."); });
    return () => { cancelled = true; };
  }, [connection]);

  useEffect(() => {
    if (connection === "offline") setMemoryPrompt(null);
    if (connection !== "offline" || !requestRef.current) return;
    window.clearTimeout(idleTimer.current);
    captureRef.current?.cancel();
    requestRef.current = null;
    stoppingRef.current = false;
    setActiveRequestId(null);
    setResponse("Connection lost. The response was interrupted; reconnect and try again.");
    setPetState("warning");
    idleTimer.current = window.setTimeout(() => setPetState("idle"), 1800);
  }, [connection]);

  useEffect(() => {
    if (!nativeAvailable || !companionWindow) return;
    const status = taskProgress?.status;
    const ended = Boolean(status && ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(status));
    if (status && !ended) {
      setPanelOpen(false);
      if (nativeMode !== "task") void nativeCommand("window_mode", { mode: "task" }).catch(() => undefined);
      return;
    }
    if (!ended) return;
    const timer = window.setTimeout(() => {
      setTaskProgress(null);
      setPanelOpen(false);
      void nativeCommand("window_mode", { mode: "pet" }).catch(() => undefined);
    }, 2200);
    return () => window.clearTimeout(timer);
  }, [taskProgress?.status, nativeMode, companionWindow]);

  async function startRecording(adoptExisting = false) {
    const options = voice?.settings;
    const capture = captureRef.current;
    if (!options || !capture || !options.voice_enabled || !options.stt_enabled || connection !== "connected") return;
    if (!["off", "error"].includes(capture.state)) return;
    window.clearTimeout(idleTimer.current);
    const requestId = crypto.randomUUID();
    requestRef.current = requestId; stoppingRef.current = false; ttsPlaying.current = false;
    speechRequested.current = options.tts_enabled;
    setActiveRequestId(requestId); setTranscript(""); setResponse(""); setNotice("");
    if (taskRef.current && ["COMPLETED", "FAILED", "CANCELLED"].includes(taskRef.current.status)) { taskRef.current = null; setTaskProgress(null); }
    setDiagnostics({ stt_language: "pending", tts_language_mode: options.tts_language_mode,
      detected_answer_language: "pending", selected_tts_voice: "pending" });
    if (!send("voice.start", { request_id: requestId, conversation_id: conversationRef.current })) { handleStop(); return; }
    try {
      const started = await capture.start({ deviceId: options.microphone_id, autoStop: adoptExisting || options.auto_stop, silenceMs: options.silence_timeout_ms, maxSeconds: options.max_recording_seconds, adoptExisting, preRollMs: options.pre_roll_ms, postRollMs: options.post_roll_ms, autoGain: options.auto_gain }, () => { void endRecording(); });
      if (started && requestRef.current === requestId) {
        send("voice.recording", { request_id: requestId });
        setPetState("listening"); setVoiceStage("MIC LISTENING");
      }
    } catch (error) { failMicrophone(error, requestId); }
  }

  function failMicrophone(error: unknown, requestId: string) {
    if (requestRef.current !== requestId) return;
    send("agent.stop", { request_id: requestId });
    const problem = microphoneError(error);
    requestRef.current = null; setActiveRequestId(null); setPetState("error"); setVoiceStage("MIC OFF");
    setNotice(`${problem.code}: ${problem.message}`);
    idleTimer.current = window.setTimeout(() => setPetState("idle"), 1800);
  }

  async function endRecording() {
    const requestId = requestRef.current;
    const capture = captureRef.current;
    if (!requestId || !capture || !["listening", "requesting"].includes(capture.state)) return;
    try {
      setPetState("thinking"); setVoiceStage("PROCESSING AUDIO · MIC OFF");
      const recording = await capture.stop();
      if (requestRef.current !== requestId) return;
      if (!recording) { handleStop(); return; }
      setDiagnostics((current) => ({ ...current, audio_duration_ms: recording.durationMs }));
      if (!send("voice.audio", { request_id: requestId, audio: audioBase64(recording.audio) })) {
        failMicrophone(new Error("Backend disconnected"), requestId);
      }
    } catch (error) { failMicrophone(error, requestId); }
  }

  const recordingActions = useRef({ startRecording, endRecording });
  recordingActions.current = { startRecording, endRecording };
  useEffect(() => {
    if (!nativeAvailable || !companionWindow || !voice?.settings.wake_word_enabled || !voice.settings.voice_enabled || !voice.settings.stt_enabled || connection !== "connected") { setWakeState("OFF"); return; }
    let current = true;
    const provider = new LocalWakeProvider(voice.settings, state => { if (!current) return; setWakeState(state); if (captureRef.current instanceof NativeMicrophoneCapture) captureRef.current.wakeRunning = ["PASSIVE","LISTENING","FOLLOWUP"].includes(state); }, message => { if (current) setNotice(message); }); wakeRef.current = provider;
    provider.on_wake(() => { void recordingActions.current.startRecording(true); });
    void provider.start().catch(() => undefined); // Provider reports a clean unavailable status.
    return () => { current = false; wakeRef.current = null; if (captureRef.current instanceof NativeMicrophoneCapture) captureRef.current.wakeRunning = false; void provider.stop(); };
  }, [voice?.settings, connection]);
  const stopAction = useRef(handleStop); stopAction.current = handleStop;
  useEffect(() => {
    if (!nativeAvailable) return;
    let disposed = false; const cleanups: (() => void)[] = [];
    const subscribe = <T,>(name: NativeEventName, callback: (value: T) => void) => { void nativeListen(name, callback).then(cleanup => { if (disposed) cleanup(); else cleanups.push(cleanup); }).catch(() => { if (!disposed) setNotice("Native controls unavailable. Restart NOVA."); }); };
    subscribe<string>(NativeEvents.hotkey, state => { if (state === "pressed") void recordingActions.current.startRecording(); else void recordingActions.current.endRecording(); });
    subscribe<string>(NativeEvents.control, action => { if (action === "stop") stopAction.current(false); });
    subscribe<string>(NativeEvents.mode, setNativeMode);
     subscribe<string>(NativeEvents.panel, panel => { if (panel === "settings") setDesktopOpen(true); else if (panel === "connections") setConnectionsOpen(true); else if (panel === "wake") setSettingsOpen(true); });
    return () => { disposed = true; cleanups.forEach(cleanup => cleanup()); };
  }, []);
  useEffect(() => {
    const code = voice?.settings.shortcut === "Alt+V" ? "KeyV" : "Space";
    let held = false;
    const down = (event: KeyboardEvent) => {
      if (event.code === "Escape") { event.preventDefault(); handleStop(); }
      if (event.metaKey && event.shiftKey && event.code === "KeyD") { event.preventDefault(); setDebugOpen((value) => !value); }
      if (!nativeAvailable && event.altKey && event.code === code && !event.repeat) { event.preventDefault(); held = true; void recordingActions.current.startRecording(); }
    };
    const up = (event: KeyboardEvent) => { if (held && (event.code === code || event.code.startsWith("Alt"))) { event.preventDefault(); held = false; void recordingActions.current.endRecording(); } };
    const blur = () => { held = false; if (!nativeAvailable && ["requesting", "listening"].includes(captureRef.current?.state ?? "off")) handleStop(); };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up); window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", blur); };
  }, [voice?.settings.shortcut]);

  function handleSubmit(override?: string) {
    const content = (override ?? text).trim();
    const currentTask = taskRef.current;
    if (content && currentTask && !["COMPLETED", "FAILED", "CANCELLED"].includes(currentTask.status)) {
      send("agent.message", { content }); setText(""); return;
    }
    if (!content || requestRef.current) return;
    window.clearTimeout(idleTimer.current);
    if (connection !== "connected") {
      setPetState("warning");
      setResponse("Backend offline. Start NOVA's local service and try again.");
      return;
    }
    const requestId = crypto.randomUUID();
    requestRef.current = requestId;
    stoppingRef.current = false;
    setActiveRequestId(requestId);
    taskRef.current = null; setTaskProgress(null);
    setResponse("");
    setNotice("");
    setMemoryPrompt(null);
    setDiagnostics({ stt_language: "not used (typed)", tts_language_mode: voice?.settings.tts_language_mode,
      detected_answer_language: "pending", selected_tts_voice: "pending" });
    speechRequested.current = Boolean(voice?.settings.voice_enabled && voice.settings.tts_enabled);
    if (!send("agent.message", { request_id: requestId, content, speak: speechRequested.current, conversation_id: conversationRef.current })) {
      requestRef.current = null;
      setActiveRequestId(null);
      setPetState("warning");
      setResponse("Backend disconnected. Reconnect and try again.");
      return;
    }
    setText("");
    setPetState("thinking");
  }

  function handleStop(requestNative = true) {
    window.clearTimeout(idleTimer.current);
    captureRef.current?.cancel();
    void wakeRef.current?.stop();
    if (captureRef.current instanceof NativeMicrophoneCapture) captureRef.current.wakeRunning = false;
    if (nativeAvailable && requestNative) void nativeCommand("stop_runtime").catch(() => undefined);
    if (taskRef.current && !["COMPLETED", "FAILED", "CANCELLED"].includes(taskRef.current.status)) send("task.stop");
    if (requestRef.current) {
      stoppingRef.current = true;
      send("agent.stop", { request_id: requestRef.current });
    }
    setPetState("idle");
    setVoiceStage("MIC OFF");
  }

  async function saveVoice(options: VoiceOptions) {
    handleStop();
    const status = await voiceStatus(options);
    localStorage.setItem("nova.voice.settings", JSON.stringify(options));
    setVoice(status);
  }

  async function openConversation(id: string) {
    handleStop();
    requestRef.current = null; stoppingRef.current = false; setActiveRequestId(null);
    taskRef.current = null; setTaskProgress(null);
    await persistence.open(id);
    send("conversation.select", { conversation_id: id });
    setResponse(""); setTranscript("");
    setMemoryPrompt(null);
  }

  async function newConversation() {
    try { const conversation = await persistenceApi.create(); await openConversation(conversation.id); }
    catch (error) { setNotice(error instanceof Error ? error.message : "New chat could not be created."); }
  }

  async function saveMemorySettings(options: MemoryOptions) {
    const settings = await persistenceApi.saveSettings(options);
    persistence.dispatch({ type: "settings", settings });
    setMemoryHint("Settings saved");
  }

  async function clearData(scope: "chats" | "memories" | "everything") {
    handleStop(); setMemoryPrompt(null);
    const result = await persistenceApi.clear(scope);
    await persistence.refresh(); await persistence.loadMemories();
    if (result.conversation) send("conversation.select", { conversation_id: result.conversation.id });
    if (scope !== "memories") { setResponse(""); setTranscript(""); }
  }

  function showMemory() {
    setHistoryOpen(false); setSettingsOpen(false); setMemoryOpen(true);
    void persistence.loadMemories().catch((error) => setNotice(error.message));
  }

  function toggleCompanionPanel() {
    if (nativeAvailable) {
      if (taskProgress && !["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(taskProgress.status)) return;
      if (companionWindow) { void nativeCommand("window_mode", { mode: "control" }).catch(() => undefined); return; }
      const next = !panelOpen;
      setPanelOpen(next);
      void nativeCommand("window_mode", { mode: next ? "control" : "pet" }).catch(() => undefined);
      return;
    }
    setPanelOpen((open) => !open);
  }

  return (
    <main className={`nova-stage ${nativeAvailable ? `nova-stage--native nova-stage--${companionWindow ? "companion" : "control"}` : ""}`}>
       {nativeAvailable && !companionWindow && <header className="native-titlebar"><span data-tauri-drag-region>NOVA <small>CONTROL CENTER</small></span><nav><button onClick={() => void nativeCommand("window_mode", { mode: "pet" })}>Companion</button><button onClick={() => void nativeCommand("open_browser_ui").catch(() => setNotice("Browser UI is unavailable."))}>Browser UI</button><button onClick={() => setConnectionsOpen(true)}>Connections</button><button onClick={() => setDesktopOpen(true)}>Desktop</button><button aria-label="Hide NOVA" onClick={() => void nativeCommand("window_mode", { mode: "background" })}>Hide</button><button className="window-control" aria-label="Minimize NOVA" onClick={() => void getCurrentWindow().minimize()}>—</button><button className="window-control" aria-label={controlMaximized ? "Restore NOVA" : "Maximize NOVA"} onClick={() => void getCurrentWindow().toggleMaximize().then(() => getCurrentWindow().isMaximized()).then(setControlMaximized)}>□</button><button className="window-control window-control--close" aria-label="Close NOVA panel" onClick={() => void getCurrentWindow().close()}>×</button></nav></header>}
       {nativeAvailable && companionWindow && <button className={`wake-indicator wake-indicator--${wakeState.toLowerCase()}`} onClick={() => { if (wakeState === "OFF" && voice?.settings.wake_word_enabled && wakeRef.current) void wakeRef.current.start().catch(() => undefined); else setSettingsOpen(true); }} aria-label="Wake-word settings">{wakeState === "PASSIVE" ? "◉ HEY NOVA · LOCAL LISTENING" : wakeState === "FOLLOWUP" ? "◉ FOLLOW-UP WINDOW" : wakeState === "LISTENING" ? "◉ LISTENING" : wakeState === "UNAVAILABLE" ? "Wake word unavailable" : `○ WAKE ${wakeState}${voice?.settings.wake_word_enabled && wakeState === "OFF" ? " · RESUME" : ""}`}</button>}
      <div className="ambient-glow ambient-glow--one" />
      <div className="ambient-glow ambient-glow--two" />
       {(!nativeAvailable || companionWindow) && <div className="companion">
          <Pet state={petState} onClick={() => { setCompanionMenuOpen(false); toggleCompanionPanel(); }} onDoubleClick={() => { setCompanionMenuOpen(false); if (companionWindow) void nativeCommand("window_mode", { mode: "control" }).catch(() => undefined); else setPanelOpen(true); }} onContextMenu={() => setCompanionMenuOpen(true)} onDragStart={() => { if (nativeAvailable) void nativeCommand("start_drag").catch(() => undefined); }} />
          {companionMenuOpen && <aside className="companion-menu" aria-label="NOVA companion menu" onPointerDown={event => event.stopPropagation()}>
            <button onClick={() => { setCompanionMenuOpen(false); void nativeCommand("open_panel", { panel: "settings" }); }}>Settings</button>
            <button onClick={() => { setCompanionMenuOpen(false); send("task.pause"); }}>Pause</button>
            <button onClick={() => { setCompanionMenuOpen(false); void nativeCommand("window_mode", { mode: "background" }); }}>Hide companion</button>
            <button onClick={() => { setCompanionMenuOpen(false); void nativeCommand("window_mode", { mode: "control" }); }}>Open panel</button>
            <button onClick={() => void nativeCommand("quit_app")}>Quit NOVA</button>
          </aside>}
        <VoiceWaveform listening={micState === "listening"} speaking={petState === "speaking" && ttsPlaying.current} level={level} />
        <div className="pet-caption">
          <span className="pet-caption__name">NOVA</span>
          <span className="pet-caption__tagline">your AI companion</span>
        </div>
       </div>}
       {(panelOpen || !companionWindow) && (
        <MiniPanel
          state={petState}
          connection={connection}
          health={health}
          response={response}
          busy={activeRequestId !== null && !taskPetRef.current}
          text={text}
          onTextChange={setText}
          onSubmit={handleSubmit}
          onMicStart={() => { void startRecording(); }}
          onMicEnd={() => { void endRecording(); }}
           voiceLabel={micState === "requesting" ? "MIC PERMISSION NEEDED" : microphoneLabel(voiceStage, voice?.settings ?? null, connection === "connected", nativeAvailable, nativeVoiceStatus)}
          voiceDisabled={!voice?.settings.voice_enabled || !voice.settings.stt_enabled}
          transcript={transcript}
          onSettings={() => { setHistoryOpen(false); setMemoryOpen(false); setSettingsOpen(true); }}
          onStop={handleStop}
          conversation={persistence.state.activeConversation}
          messages={persistence.state.messages}
          onHistory={() => { setMemoryOpen(false); setSettingsOpen(false); setHistoryOpen(true); }}
          onNewChat={() => { void newConversation(); }}
          onEarlier={() => { void persistence.loadEarlier().catch((error) => setNotice(error.message)); }}
          onMemory={showMemory}
          memoryHint={memoryHint}
           onComputer={() => { setSettingsOpen(false); setMemoryOpen(false); setHistoryOpen(false); setComputerOpen(true); }}
           onConnections={() => { setSettingsOpen(false); setMemoryOpen(false); setHistoryOpen(false); setComputerOpen(false); setConnectionsOpen(true); }}
           onModels={() => { setSettingsOpen(false); setMemoryOpen(false); setHistoryOpen(false); setComputerOpen(false); setConnectionsOpen(false); setModelsOpen(true); }}
         />
      )}
      {notice && <p className="voice-notice" role="alert">{notice}</p>}
      {taskProgress && <div className="task-panel"><TaskProgress task={taskProgress} watchEnabled={computerOptions?.watch_nova} onControl={(type, payload) => send(type, payload)} /></div>}
      {computerOpen && <ComputerSettings onClose={() => setComputerOpen(false)} onSave={(settings) => { setComputerOptions(settings); setNotice("Computer settings saved."); }} />}
      {desktopOpen && nativeAvailable && <DesktopSettings onClose={() => setDesktopOpen(false)} />}
       {connectionsOpen && <ConnectionHub onClose={() => setConnectionsOpen(false)} />}
       {modelsOpen && <ModelSettings onClose={() => setModelsOpen(false)} />}
       {onboardingOpen && <Onboarding health={health} voice={voice} onVoice={() => { setOnboardingOpen(false); setSettingsOpen(true); }} onComputer={() => { setOnboardingOpen(false); setComputerOpen(true); }} onConnections={() => { setOnboardingOpen(false); setConnectionsOpen(true); }} onClose={() => setOnboardingOpen(false)} />}
      {settingsOpen && voice && <VoiceSettings status={voice} onSave={saveVoice} onClose={() => setSettingsOpen(false)} onMemory={showMemory} />}
      {memoryOpen && persistence.state.memorySettings && <MemorySettings settings={persistence.state.memorySettings} memories={persistence.state.memories}
        onSave={saveMemorySettings} onReload={persistence.loadMemories} onClear={clearData} onClose={() => setMemoryOpen(false)}
        onVoice={() => { setMemoryOpen(false); setSettingsOpen(true); }} />}
      {historyOpen && <HistoryPanel conversations={persistence.state.conversations} onOpen={openConversation} onNew={newConversation} onChange={() => persistence.refresh()} onMore={persistence.loadMoreConversations} onClose={() => setHistoryOpen(false)} />}
      {memoryPrompt && <aside className="memory-confirmation" aria-label="Memory confirmation"><div className="persistence-actions">
        {memoryPrompt.action === "clear" ? <button disabled={activeRequestId !== null} onClick={() => handleSubmit("Yes, clear my saved memories.")}>Confirm clear memories</button>
          : memoryPrompt.choices?.map((choice, index) => <button disabled={activeRequestId !== null} key={index} onClick={() => handleSubmit(String(index + 1))}>{index + 1}. {choice}</button>)}
        <button disabled={activeRequestId !== null} onClick={() => handleSubmit("Cancel")}>Cancel</button>
      </div></aside>}
       {debugOpen && <VoiceDebug data={{
         HEARD: diagnostics.transcript ?? "—", INTENT: diagnostics.normalized_command ?? diagnostics.task_goal ?? "—", TASK: diagnostics.task_id ?? "—",
         STEP: diagnostics.current_step ?? "—", APP: (diagnostics.pipeline_trace as Record<string, unknown> | undefined)?.requested_application ?? diagnostics.active_application ?? "—",
         TOOL: (diagnostics.pipeline_trace as Record<string, unknown> | undefined)?.selected_tool ?? "—", RESULT: diagnostics.failure ? "FAILURE" : diagnostics.task_status === "COMPLETED" ? "SUCCESS" : "—",
         ...diagnostics, conversation_id: persistence.state.activeConversation?.id, message_count: persistence.state.activeConversation?.message_count,
        computer_mode: computerOptions?.mode, computer_simulation: computerOptions?.simulation,
         database_ready: persistence.state.database?.available, microphone: micState, microphone_permission: nativeVoiceStatus?.microphone_state, speech_permission: nativeVoiceStatus?.speech_recognition_state, permission_owner: nativeAvailable ? "local.nova.voice" : "browser microphone permission", voice_stage: voiceStage, wake_state: wakeState, wake_provider: voice?.settings.wake_provider, stt_ready: voice?.stt.available, tts_ready: voice?.tts.available, websocket: connection, request_id: activeRequestId }} />}
      <p className="phase-note">NOVA · Option+Space to talk · Esc to stop</p>
    </main>
  );
}

export default App;
