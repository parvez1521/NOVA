# NOVA architecture

## Runtime shape

```text
React pet shell (Vite)
        │ REST: health/settings/memory
        │ WebSocket: live status, streaming, cancellation
        ▼
FastAPI orchestration service
        ├── Context manager + short-term conversation buffer
        ├── Agent loop with bounded steps
         ├── Model registry + SmartModelRouter
         │     ├── Ollama / discovered local models (default)
         │     ├── Gemini / live account model listing
         │     └── OpenRouter / live pricing catalog and free fallback
        ├── Permission-aware tool registry
        ├── Speech adapters (local STT / native TTS)
         ├── SQLite chat history and separate long-term memory
         ├── Connector registry + secure credential boundary
         └── OS adapters (macOS first)
```

## Boundary rules

1. The frontend owns presentation, pet animation, user gestures, and non-sensitive display state.
2. The backend owns providers, API keys, agent decisions, tool execution, SQLite, and permission checks.
3. WebSocket messages are event envelopes with a `type`, UTC `timestamp`, and JSON `data` object.
4. REST is used for request/response resources such as settings and memory management.
5. Every capability reports availability explicitly. A missing optional dependency never prevents startup.
6. Tool execution is never authorized merely because a model requested it. The permission layer is authoritative.

## Phase 2 modules

- `backend/app/main.py`: application factory, real provider health, request lifecycle, WebSocket streaming, and cancellation.
- `backend/app/core/config.py`: environment-backed settings and safe public configuration.
- `backend/app/llm/`: provider contract, model descriptors/registry, normalized errors, Ollama NDJSON/discovery, Gemini REST/SSE, OpenRouter SSE/catalog, pricing policy and SmartModelRouter.
- `backend/app/agent/`: centralized system prompt, bounded conversation context, and conversation streaming agent.
- `frontend/src/components/Pet/`: state-driven visual pet surface.
- `frontend/src/hooks/useNovaConnection.ts`: backend health, WebSocket lifecycle, and event delivery.
- `frontend/src/App.tsx`: streamed response rendering and backend-driven pet state transitions.

## Planned build order

1. Project architecture and health bridge — complete
2. Ollama chat streaming, provider tests, OpenRouter fallback, and model routing — complete
3. Local STT, push-to-talk, language-aware native TTS, speech cleanup and interruption — complete; physical-microphone/listening acceptance confirmed by the user on 2026-10-05
4. Optional Piper adapter
5. Tool registry, schemas, OS adapters, and command safety — implemented in Phase 5
6. SQLite chat history, extraction policy, memory UI and restart restoration — complete; actual backend restarts and user-confirmed microphone memory commands verified on 2026-10-05
7. Bounded tool-capable agent loop, shared task voice controls and verification — implemented; real-machine checks passed, final physical-microphone computer-task acceptance pending
8. Tauri packaging, bundled local runtime and global microphone shortcuts — implemented; final physical-microphone computer-task acceptance remains pending

## WebSocket event contract

```json
{
  "type": "system.ready",
  "timestamp": "2026-10-04T00:00:00Z",
  "data": {
    "service": "nova-backend",
    "phase": 1
  }
}
```

Current accepted client messages are `ping`, `agent.message`, `agent.stop`, `voice.start`, and `voice.stop`. `chat.message` and `agent.cancel` remain compatibility aliases. Unknown messages receive a safe acknowledgement so the UI can evolve without crashing the connection.

## Phase 2 event flow

```text
agent.started
agent.thinking
llm.started
llm.token × N
llm.completed
agent.completed
```

Every request carries a UUID `request_id`. Errors use stable codes such as `NO_LLM_PROVIDER`, `LLM_MODEL_NOT_FOUND`, and `LLM_TIMEOUT`. Cancelling an active request emits `agent.cancelled` and cancels the underlying async generation task.

### Thinking policy and answer streaming

`agent/thinking.py` performs deterministic request classification independently of provider routing. It selects `think:false` for simple/normal conversation, `think:true` for explicit complex reasoning, with configurable forced `off`/`on` modes. The decision and budget are passed through the existing provider interface; provider instances are never mutated per request.

Ollama uses the native `/api/chat` `think` field. Qwen3 `/api/show` thinking metadata is cached briefly and checked before incompatible requests. The hybrid `qwen3:4b-q4_K_M` is the configured default because the moving `qwen3:4b` tag can be Thinking-only. No model is installed automatically by startup.

`llm/answer.py` incrementally strips inline reasoning while the Ollama/OpenRouter adapters ignore dedicated reasoning fields. Only visible answer content is yielded, persisted in short-term context, spoken, or sent over WebSocket. TTFT, generation latency, thinking decision and visible chunk counts are metadata on existing `llm.started`/`llm.completed` events, shown only in the debug panel. Existing event ordering, routing, cancellation and token streaming remain the same.

## Phase 3 voice architecture

```text
  Focused button / native global shortcut / local wake phrase
  → getUserMedia (only while held)
  → AudioWorklet PCM capture / optional energy VAD
  → browser local resampling to mono 16 kHz WAV
  → voice.audio (bounded base64, same WebSocket)
  → local Whisper subprocess (Metal, on demand)
  → voice.transcript
  → existing Agent + ConversationContext + ProviderRouter
  → existing llm.token events
  → streaming SpeechTextCleaner → SentenceBuffer → SpeechQueue
  → LanguageResolver → VoiceRegistry → explicit macOS say voice
```

`voice/base.py` defines validated settings and structured errors. `voice/stt/base.py` defines provider-independent transcription results. `LocalWhisperProvider` uses the already-installed whisper.cpp backend; no audio goes to an LLM provider. `voice/tts/base.py` describes cancellable local playback. `MacOSTTSProvider` passes sanitized text through stdin, never a shell command. `voice/process.py` terminates, kills on timeout if needed, and reaps children on cancellation.

`VoiceManager` has shared STT/TTS locks to avoid concurrent model loads and overlapping audio between browser sessions. Each request snapshots options and owns its speech queue. Capture/model temporary data is discarded after use. The short-term conversation stays scoped to the existing WebSocket Agent session; both input modes commit to it.

The packaged Tauri shell starts in compact pet mode. The frontend keeps the
control panel closed by default in native mode, requests the task-sized native
window while a real computer task is active, and returns to the pet window
after completion. The browser development shell retains its existing panel-first
workflow. Native wake matching accepts complete “Hey Nova” and “Hello Nova”
phrases only; a bare “Nova” is never sufficient.

`SpeechTextCleaner` is speech-only: visible tokens and conversation context stay intact. It suppresses emoji, markup, private tags, JSON and code before sentence splitting, carrying bounded fence/private-block state across chunks. URLs use a link phrase unless explicitly requested aloud. The native adapter also cleans direct calls and skips empty speech. `LanguageResolver` uses cleaned answer segments, not STT hints, with deterministic Devanagari/roman-Hindi heuristics and explicit language overrides. Segment resolution keeps sentence-first streaming responsive. `VoiceRegistry` caches the actual installed `say` inventory behind a lock; locale-checked English/Hindi preferences, Hinglish Indian-English preference, and safe fallback prevent accidental Chinese/system-default playback. No classifier API, extra model or cloud TTS is involved.

### Multi-model intelligence

`llm/models.py` defines capability metadata where `None` means unknown,
pricing states, the live registry, deterministic task heuristics and bounded
latency statistics. `ProviderRouter` refreshes provider catalogs, filters by
required capabilities before scoring, prefers local models under `LOCAL_FIRST`,
and carries a primary-to-tertiary fallback chain. `FREE_ONLY` and
`ZERO_BUDGET_MODE` reject both paid and unknown cloud pricing. OpenRouter
requests additionally send `provider.max_price` zero limits; Gemini account
model listing does not claim pricing, so Gemini stays out of zero-budget routes
unless a future verified pricing source is added. No extra LLM request is used
to classify ordinary task type.

### Events and timing

Client: `voice.start`, `voice.recording`, `voice.audio`, `agent.stop` (or legacy `voice.stop`).

Server: `voice.listening`, `voice.recording`, `voice.processing`, `voice.transcript`, plus unchanged Phase 2 agent/LLM events and `tts.started`, `tts.sentence`, `tts.sentence_completed`, `tts.completed`, `tts.cancelled`. Additive `tts.cleanup` reports final original/cleaned speech lengths, including empty emoji-only speech.

`tts.sentence` carries detected answer language, resolved language/mode, uncertainty, selected native voice/locale and fallback status. The debug panel keeps STT language separate and identifies resolution as scoped to the cleaned speech segment. Cleanup metrics are code-point lengths, not tokenizer counts; `speech_chars_removed` reports nonnegative net length reduction, not a fabricated exact edit count.

Every interaction retains one request UUID. TTS events can interleave with `llm.token`: speech must begin as soon as a sentence is available, **before** LLM completion when possible. `agent.completed` follows both LLM completion and draining speech. `system.error` with `recoverable:true` indicates speech output degraded to text; the agent continues.

### Cancellation and privacy

STOP cancels active capture, transcription, LLM stream and queued/playing TTS. A new `voice.start` preempts the prior request, waits for resource cleanup, then starts a new request ID. Disconnects stop processes and recording tracks. Recording expires server-side if audio never arrives, and the browser has a hard 60-second cap. Permission-pending release stops tracks when permission eventually resolves.

Only allowed frontend origins can use the browser WebSocket. `/api/voice` validates public settings; `/api/voice/voices` lists installed native voices. Runtime settings plus browser-local preferences preserve settings without SQLite. No raw audio or transcripts are logged. The debug panel is in-memory only.

## Persistent conversation and memory layer

`agent/conversations.py` owns SQLite repositories/settings and bounded context restoration. Shared text/voice entry points persist sanitized user input immediately, run deterministic memory commands/extraction, retrieve a bounded set of relevant active facts, and invoke the existing Agent with compact memory context. Successful generation stores one assembled assistant message; cancelled/failed generations retain only their user input. Existing streaming, reasoning suppression, provider routing/thinking and speech cancellation are retained.

`database/` uses standard-library SQLite, transactional migrations, WAL, parameterized repositories, FTS5 indexes with LIKE fallback and a credential/reasoning persistence boundary. `memory/` handles deterministic commands, confidence-gated extraction, canonical duplicate/conflict replacement, retrieval and session-scoped confirmation. Chat records are distinct from memories; inactive memory-associated turns are omitted from replayed model context while retained in visible history.

`usePersistence` is the frontend's centralized conversation/messages/memories/settings state. History and Memory are compact panels in the existing shell. The connection hook preserves every-frame token delivery and reconnects after backend restarts. Destructive actions require confirmation; exports stay local. See [PERSISTENCE.md](PERSISTENCE.md) for schema, routes, restart acceptance and performance evidence.

## Phase 5 computer-task runtime

`computer/runtime.py` owns the shared text/voice observe → structured plan → permission → execute → observe → verify loop. `TaskManager` tracks one global active desktop task, owner connection, approvals, pause, timeout and cancellation. `TaskPlanner` uses the existing local provider's additive structured-output method; it proposes one registered action at a time against a compact observation/evidence ledger. The independent `TaskExecutor` validates tool schemas, credential guards, folder scope, enabled capabilities, confidence/freshness and approval before dispatch.

Native controllers call a fixed JSON-protocol Swift helper for applications, AX elements, CGEvents, clipboard, temporary ScreenCaptureKit images/Vision OCR and listen-only global Escape. Browser tools use fixed CDP operations in a visible dedicated Chrome profile. Filesystem tools use exclusive writes/copies/moves and nonrecursive deletion. `VerificationEngine` reads actual app state, DOM/URLs, file contents, clipboard, pointer and target editable-field values, independently of labels/placeholders; completion is checked against the full goal. Cross-label/value secret guards sanitize observations and known security content blocks capture. Native browser input and embedded-terminal typing cannot bypass registered tools. Retry/replanning/step/time limits prevent blind action loops; repeated cancellation preserves cleanup and task ownership until key release finishes.

The existing WebSocket adds task events/control without replacing conversation or voice infrastructure. User follow-ups and spoken controls persist in chat; task summaries use the additive schema-2 `tasks` table. Settings, compact progress/confirmation, pet states and diagnostics live in the existing shell. Screenshots remain temporary; optional image models are installed and loopback-local only. See [COMPUTER_USE.md](COMPUTER_USE.md) for detailed boundaries and acceptance evidence.

## Connector foundation

`connectors/` is independent of the core agent. `Connector` defines metadata,
health, connect/disconnect/refresh and tool discovery; `ConnectorManager`
registers Google, GitHub, Notion, Telegram, LinkedIn, Instagram, YouTube,
Slack and Discord honestly, with Google supplying read-only Gmail OAuth and
the others remaining planned. The REST management API exposes only sanitized
metadata and lifecycle state. OAuth/API credentials are intentionally outside
SQLite and the frontend; `MacOSKeychainStore` is the production boundary and
tests use an in-memory store. Google Gmail search/read tools are registered in
the existing guarded task executor only when connectors are enabled.

`MCPManager` is a separate explicit local stdio boundary. A server must be
registered with confirmation, enabled with confirmation, discovered, inspected
and confirmed again for each tool execution. Shell syntax, unknown tools,
credential-bearing arguments and unbounded messages are blocked. Sessions are
terminated on disable and application shutdown.
