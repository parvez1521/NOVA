# NOVA development progress

Updated: 2026-10-07

## Resume point

NOVA is an existing local-first macOS companion at `/Users/parvez/NOVA`. This
directory is not a Git repository, so progress is tracked here and in the
project documentation. The previous implementation session completed the
native Tauri packaging pass; this session resumes with integrations rather
than rebuilding the core assistant.

## Already complete and verified

- **Phase 1 foundation:** FastAPI backend, React/Vite shell, WebSocket event
  transport, configuration, health endpoints, setup scripts and baseline
  tests.
- **Phase 2 local AI:** Ollama/Qwen3 hybrid routing, OpenRouter fallback,
  smart thinking policy, answer-only streaming, cancellation, normalized
  provider errors and latency diagnostics. Do not redesign the LLM path.
- **Phase 3 voice:** local Whisper STT, 16 kHz microphone capture, VAD,
  preprocessing/gain, pre/post-roll, English/Hindi/Hinglish handling,
  native macOS TTS, speech cleanup, language-aware voices and interruption.
  Physical microphone acceptance for ordinary voice interaction passed.
- **Phase 4 persistence:** SQLite chat history, long-term memory, commands,
  extraction/retrieval/conflict handling, search, exports, UI and restart
  restoration. Physical microphone remember/forget acceptance passed.
- **Phase 5 computer foundation:** guarded tools, native macOS and Chrome
  controllers, scope and secret guards, permissions, simulation, bounded
  observe/act/verify/replan loop, pause/resume/stop, task history and crash
  interruption handling.
- **Wake-word implementation:** local `WakeWordProvider` contract, native
  Apple on-device speech provider, local Whisper VAD fallback, explicit OFF
  default, follow-up window and return to passive listening. Physical
  hands-free acceptance is not yet confirmed.
- **Live task experience:** task timeline/progress events, opt-in view-only
  active-window preview, temporary screenshot cleanup, preview cancellation,
  cursor visibility modes (`FAST`, `NORMAL`, `HUMAN_VISIBLE`) and smooth native
  pointer movement.
- **Native desktop packaging:** Tauri app, bundled PyInstaller backend, local
  Whisper resources/model, native helper, native microphone/wake helper,
  tray menu, launch-at-login option, notifications, global shortcut, global
  Escape and DMG build. The app bundle and DMG were built, signed with an
  ad-hoc identity, verified, and passed packaged runtime smoke testing.
- **Native companion mode:** the packaged shell now starts as a compact pet,
  opens the control center only when requested, switches to a task-sized view
  during active computer work, and returns to pet mode after completion.
- **Wake phrase coverage:** local native detection now accepts both “Hey Nova”
  and “Hello Nova” while retaining complete-phrase matching and sensitivity
  thresholds. A short local “Yeah?” acknowledgement plays before command
  capture begins.
- **STT profiles:** Voice settings now expose explicit `FAST` (Base),
  `BALANCED` (Small) and `ACCURATE` (Medium) local Whisper profiles. The
  selected profile is persisted through `/api/voice` and controls the model
  used for transcription; no model is downloaded automatically.
- **Regression baseline before this milestone:** 302 backend tests and 4 frontend tests pass;
  frontend typecheck/build, Python compilation, shell checks and packaged
  smoke checks pass.

## Partially complete

- **Computer-use acceptance:** automated and real-machine checks passed, but
  the final physical-microphone task sequence (open YouTube, create a folder,
  pause/continue/stop an approval-bound clipboard read) remains a user-level
  acceptance step.
- **Hands-free mode:** the implementation exists and is native/local, but
  physical “Hey Nova”/“Hello Nova” wake, command capture, follow-up and spoken task-control
  acceptance are still pending.
- **Live task view:** the backend and compact panel are implemented, but live
  preview is deliberately opt-in and the native packaged UI still needs a
  polished end-to-end visual acceptance pass.
- **Native product UX:** tray/background/desktop settings and Connections hub
  exist; first-run onboarding now provides the local setup walkthrough.

## Missing

- Provider OAuth callback/state flow and provider-specific token refresh for
  services other than Google.
- Google write capabilities (draft/send/create/update/delete/upload) remain
  intentionally unimplemented and confirmation-gated for a future milestone.
- Final premium connection/onboarding UX and connector diagnostics.

## Blocked or requiring user action

- Physical microphone/wake/computer-task acceptance requires the user and a
  real Mac session with microphone, Accessibility and Screen Recording grants.
- OAuth connector acceptance requires user-supplied app credentials and an
  account authorization; tests must remain mocked/offline.
- Native distribution signing/notarization requires the user's Apple
  signing/notarization credentials. Local ad-hoc packaging is working.
- Google OAuth requires the user's Google Cloud OAuth client configuration and
  account authorization; no real account was accessed during automated tests.

## This session's connector milestone

Implemented the provider-independent connector contracts and registry,
Keychain credential boundary, feature-flagged REST metadata/lifecycle routes,
honest planned-provider statuses, Connections hub UI and backend contract/API
tests. This resumed milestone added Google OAuth callback/state validation,
Keychain-backed filtered token storage, read-only Gmail search/read, guarded
task-planner tools, explicit MCP stdio registration/discovery/execution, and a
first-run onboarding flow. No send, draft, publish, delete or account-mutating
provider action was added.

Final verification after the connector milestone: **300 backend tests and 4
frontend tests pass**; frontend typecheck/build, Python compilation, shell
checks, native Tauri packaging, strict app signature validation, DMG
verification, native voice self-test and packaged smoke (including the
feature-flagged connector and MCP endpoints) pass. The native pet-mode and
alternate wake-phrase change was then verified with targeted wake tests and
frontend typecheck/build.

Final verification after the native companion milestone: **303 backend tests
and 4 frontend tests pass**; native Swift typecheck/self-test, frontend
typecheck/build, desktop packaging, strict app signature validation, DMG
verification and packaged smoke all pass. The packaged artifact includes
compact pet mode, task-mode resizing, “Hey Nova”/“Hello Nova” matching and
the local “Yeah?” wake acknowledgement.

The STT profile milestone passes the focused voice suite (**18 tests**), full
backend regression, frontend test/typecheck/build, desktop packaging and
packaged smoke. The artifact also includes the persisted FAST/BALANCED/ACCURATE
profile selector.

## Current next action

Gemini/OpenRouter credentials are now supplied through `backend/.env`. The
explicit live model tests have been run and recorded below. Next, complete the
provider-generation decision from the account responses, then authorize GitHub
and perform the native physical wake/computer-use/pause/continue/stop
acceptance. Continue with Notion only after GitHub real acceptance is recorded.

## Multi-model gap report at milestone start — 2026-10-06

The following categories describe the state before the implementation below;
the resolved model-routing items are recorded in the milestone section after
the report.

### Already complete

- Local Ollama/Qwen3 streaming, smart thinking, OpenRouter streaming fallback,
  cancellation, normalized errors and per-request TTFT/generation diagnostics.
- Local Whisper STT, native macOS TTS, Hindi/Hinglish handling, explicit STT
  profiles, native wake flow, computer-use planning/execution and guarded tools.
- Google read-only connector foundation, Keychain boundary, MCP safeguards,
  native pet shell, task view and packaging.

### Partially complete

- `LLMProvider` supports chat generation but exposes no model capabilities,
  pricing state, context limits, modality metadata or structured availability.
- `ProviderRouter` chooses between one configured Ollama model and one
  configured OpenRouter model using a small static policy; it is not task,
  capability, privacy, latency or model-registry aware.
- Ollama health checks only the configured model. Installed-model discovery and
  uncertain capability metadata are not surfaced.
- OpenRouter is configured as an optional fallback, but current catalog/free
  eligibility and model capability discovery are absent.
- Latency is reported per request but not retained as rolling model statistics.
- STT preprocessing and vocabulary are local and working; cloud STT fallback,
  microphone diagnostics, Piper and optional Gemini speech providers are absent.

### Missing

- `ModelRegistry` and capability-aware model descriptors.
- Official `GoogleGeminiProvider` and live Gemini model discovery.
- Live OpenRouter model catalog with free-pricing eligibility and cache state.
- `SmartModelRouter`, requested routing modes, fallback chains and deterministic
  task-type detection.
- `ZERO_BUDGET_MODE=true`, `FREE_ONLY=true`, paid-cloud blocking and clear
  no-eligible-model errors.
- Optional jury mode, model-aware tool selection and AI Models/routing UI.
- Real provider adapters beyond Google: GitHub, Notion, Telegram, LinkedIn,
  Instagram, YouTube, Slack and Discord.

### Broken or unsafe for the target

- A configured OpenRouter key currently makes the selected model appear
  available without checking live pricing, capability or catalog eligibility.
  This is not safe for free-only/zero-budget operation and must be corrected
  before cloud routing is expanded.
- The existing `llm_routing_policy` values do not implement the requested
  `LOCAL_ONLY`, `FREE_ONLY`, `BALANCED`, `BEST_AVAILABLE` and `OFFLINE` modes.

### Blocked

- Gemini discovery and generation require a user-supplied `GEMINI_API_KEY`.
- OpenRouter free catalog verification requires a configured API key and live
  network access; unknown pricing must remain `UNKNOWN`, never be treated as
  free.
- Real connector acceptance requires provider credentials, account grants and
  external API approval where applicable. Automated tests remain mocked.

## Real activation / GitHub milestone — in progress

Implemented and mocked-verified:

- Safe model activation diagnostics via `/api/models` activation metadata and
  explicit `POST /api/models/live-test`. These routes expose configuration and
  sanitized response metadata only; they never return keys.
- Backend-only Gemini/OpenRouter configuration remains environment-driven and
  free/zero-budget policy gates remain active during live tests.
- First real provider adapter: GitHub official OAuth with state validation,
  filtered secure credential storage, health/status, disconnect, re-auth and
  permission/rate-limit error states.
- GitHub read tools: profile, repositories, repository search, file reads,
  issues, pull requests and branches. No GitHub write/delete tool is exposed.
- GitHub is now registered as implemented in the real ConnectorManager path;
  the Connections panel can display its setup state and OAuth action.

Verification after this milestone: **309 backend tests pass**, including the
GitHub OAuth/API/secret-boundary suite and activation-policy tests. Real Gemini,
OpenRouter and GitHub account acceptance remains blocked until the user supplies
credentials and authorizes accounts. Native packaging, strict signature
validation, DMG verification, native voice self-test and packaged smoke pass
with the GitHub adapter included.

## Multi-model intelligence milestone — in progress

Implemented and verified in this continuation:

- `ModelCapabilities`, `ModelDescriptor`, `ModelRegistry`, deterministic task
  detection and bounded rolling latency statistics.
- Ollama installed-model discovery through `/api/tags` plus `/api/show` metadata
  for the configured models; no model downloads are performed.
- Official Gemini REST provider with backend-only API key handling, account
  model listing, method/token/thinking metadata and SSE generation. Gemini
  pricing remains `UNKNOWN` because the official model list does not establish
  account free-tier eligibility.
- OpenRouter live `/api/v1/models` catalog discovery, sanitized local cache,
  current prompt/completion/request pricing parsing, capability extraction and
  server-side zero-price request limits.
- Capability/privacy/pricing/availability/latency-aware `ProviderRouter` with
  task requirements, fallback chains, `LOCAL_ONLY`, `FREE_ONLY`, `BALANCED`,
  `BEST_AVAILABLE` and `OFFLINE` behavior, zero-budget enforcement and no
  multi-model call for ordinary requests.
- `/api/models`, `/api/models/refresh`, `/api/routing` and the frontend **AI
  Models** panel. Provider secrets never leave the backend.
- Computer cloud-planning selection now requests tool and structured-output
  capabilities while retaining the existing local planner preference.

Focused routing tests pass (**49**), the full backend regression passes (**310**),
and the frontend tests/typecheck/build pass (**4**). Native Swift self-test,
Tauri packaging, strict signature validation, DMG verification and packaged
smoke also pass after the final AI Models UI review.

Still missing from the multi-model target: optional Gemini transcription/TTS,
Piper, jury mode execution, richer vision/audio provider routing, and real
provider adapters beyond Google. Those remain separate milestones and are not
represented as complete here.

## Current milestone verification matrix — 2026-10-06

### Implemented

- Gemini/OpenRouter live-test endpoint and backend-only activation metadata.
- Official GitHub read adapter and official Notion read adapter through the
  existing ConnectorManager.
- Connector status extensions for re-authentication, permission denial, partial
  access and error reporting.
- Notion official integration-token configuration:
  `NOTION_API_TOKEN` and `NOTION_VERSION`.

### Real-account / real-network verified

- `backend/.env` is loaded by the backend without exposing values; both
  Gemini and OpenRouter report `configured: true`, with `FREE_ONLY=true` and
  `ZERO_BUDGET_MODE=true` still active.
- Gemini account model discovery reached the official API and returned HTTP
  200 with 61 available models. The pricing-policy layer now recognizes only
  the exact configured Free Tier IDs that are also present in live discovery:
  `gemini-3.8-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`,
  `gemini-3.1-flash-lite`, `gemini-2.5-flash` and
  `gemini-2.5-flash-lite`. The source is Google’s official pricing page:
  <https://ai.google.dev/gemini-api/docs/pricing>.
- Gemini real generation succeeded with `gemini-3.8-flash`: HTTP 200, a
  non-empty response, `FREE` pricing state and approximately 12.2 seconds
  generation latency. The account returned HTTP 404 for the older 2.5 IDs
  with a “no longer available to new users” response; bounded free-model
  selection continued to the currently usable allowlisted ID. No paid or
  unknown model was attempted.
- OpenRouter discovery reached the official API and returned HTTP 200 with
  648 available models, including 67 explicitly free models and 20 free text-
  streaming models. The official `openrouter/free` route is exposed only when
  the live catalog proves at least one free text-streaming candidate. A live
  generation request now succeeded through `openrouter/free` with HTTP 200, a
  non-empty response and approximately 3.6 seconds generation latency.
- The earlier real OpenRouter request that returned a rate limit is now
  classified as `LLM_RATE_LIMIT`; bounded fallback tries at most three
  eligible free candidates, applies a short same-provider backoff, records a
  model cooldown and then falls back to local Ollama when available. Paid
  models remain excluded by both policy gates.
- No API key, authorization header, provider response body containing secrets,
  or credential value was written to the API response or progress log.

### Mock-tested only / additionally covered by regression tests

- Gemini explicit-price/allowlist policy, generation-endpoint fallback and
  OpenRouter catalog/streaming/rate-limit behavior have regression coverage;
  provider-specific account behavior is recorded above.
- GitHub OAuth, profile, repository, search, file, issue, pull-request and
  branch tools.
- Notion authorization, search, page retrieval and block-content reads.
- Connector secret-boundary, permission and unsupported-operation behavior.

### Physically tested

- Existing ordinary local microphone, Hindi/Hinglish, native packaging and
  native helper checks remain recorded above.
- Native wake, hands-free task, real cursor, live preview, pause/continue/stop,
  real OAuth and cross-connector acceptance are not physically tested in this
  milestone.

### Blocked by credentials

- GitHub OAuth, Notion account authorization and Google OAuth account
  acceptance remain pending.
- Gemini and OpenRouter generation are now real-account verified for the
  current run. Provider rate limits can still temporarily make free routing
  unavailable; the router reports that state without paid fallback.

### Blocked by platform permissions

- Native microphone wake/task acceptance requires the production NOVA.app with
  microphone, Accessibility and Screen Recording permissions.

### Unsupported by official API

- Notion writes/deletes are not exposed by the current adapter.
- GitHub writes/deletes are not exposed by the current adapter.
- Telegram, LinkedIn, Instagram, YouTube, Slack and Discord adapters are not
  implemented yet; no private/unofficial endpoints are used.

### Known limitations

- Gemini model-list metadata still does not include pricing. Free eligibility
  is therefore granted only through the documented exact-ID allowlist plus
  live discovery; all other Gemini models remain `UNKNOWN` and blocked.
- Notion currently uses an official internal integration token rather than a
  public OAuth flow.
- API-first versus browser fallback is not yet unified for connector tasks.

### Exact next step

Continue with GitHub account authorization and native physical acceptance.
Keep monitoring OpenRouter free-model rate-limit state through the bounded
fallback path before adding Telegram.

Final verification for this provider-policy milestone: **317 backend tests**, **4
frontend tests**, frontend typecheck/build, Python compilation, native voice
self-test, Tauri packaging, strict signature validation, DMG verification and
packaged runtime smoke all pass. This is automated/package verification only;
real Gemini and OpenRouter generation succeeded in the current live run; real
connector and physical native acceptance remain pending.

## Real activation and physical acceptance gap report — 2026-10-06

This section records the pre-credential gap state; the current verification
matrix above supersedes its cloud-credential status.

### Already complete

- Gemini and OpenRouter provider implementations, model discovery, pricing
  safeguards, routing, fallback, backend-only secret fields and model settings.
- Local Ollama routing and fallback, Google read-only OAuth/connector tools,
  guarded MCP, native pet/wake/voice/computer-use foundations and packaged
  runtime verification.

### Partially complete

- Gemini is implemented but has no configured API key in this environment, so
  only mocked discovery/generation tests exist. The current official model list
  does not prove account free-tier eligibility; unknown pricing remains blocked
  by default.
- OpenRouter catalog and streaming are implemented but no API key is configured,
  so live catalog/request/fallback/rate-limit behavior remains unverified.
- Google is the only real connector adapter. Its read-only API path is mocked;
  OAuth/account acceptance is still pending.
- The unified TaskManager and browser/API tool boundaries exist, but connector
  adapters beyond Google are not registered as implemented.

### Missing

- Secure real-provider activation workflow/diagnostics for Gemini and
  OpenRouter, including explicit configuration status and live-test evidence.
- Real GitHub adapter, followed by Notion, Telegram, LinkedIn, Instagram,
  YouTube, Slack and Discord adapters.
- Real-account connector capability/permission verification and API-first versus
  browser fallback task coverage.
- Physical native tests for wake, hands-free tasks, live cursor, pause/resume,
  stop and the final cross-connector workflow.

### Broken or unsafe for real activation

- A live-provider test must never be inferred from configured environment values;
  the current environment has neither cloud key. No live request will be claimed
  until a configured account returns a non-empty response with provider/model
  diagnostics and no secret leakage.
- Gemini unknown pricing must not enter default free-only routing. Explicitly
  disabling free-only and zero-budget policies is required before any unknown-
  priced Gemini generation can be selected.

### Blocked

- `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, OAuth client credentials and real
  connector accounts are user-supplied and must not be invented or written to
  SQLite/frontend state.
- Physical acceptance requires the user’s production NOVA.app session,
  microphone, Accessibility, Screen Recording and account authorizations.

## COMPUTER-USE INTELLIGENCE — 2026-10-06

### Why the first action was being treated as completion

The existing computer loop already had observe/act/verify hooks, but the LLM
planner received an action-shaped contract and was responsible for inferring
the entire goal on every round. A weak or premature `complete` decision after
`app.open` could therefore stop a compound request. Browser tooling also
defaulted to the dedicated Chrome profile, so an explicit Safari request could
be silently routed through the wrong application.

### Implemented

- Added a structured `ComputerIntent` contract containing:
  - user goal
  - target application and bundle ID
  - target entity/object
  - requested message content when supplied
  - intended action
  - bounded ordered steps
  - success condition
  - verification strategy
  - risk and confirmation requirement
  - missing-information state
  - entity-resolution state
- Added deterministic Hinglish/English parsing for conjunctions such as
  `aur`, `phir`, `karke`, `then`, `after that`, `open and`, `find and`, and
  `search and`.
- Added explicit application resolution for Safari, Google Chrome, Arc,
  Finder, Terminal, WhatsApp, Telegram, and System Settings using bundle IDs.
- Updated the native helper’s application map and observations to include the
  active bundle ID. Explicit app requests are validated before execution;
  Chrome cannot replace Safari.
- Added application-aware browser tools:
  - Chrome continues to use the existing dedicated CDP profile.
  - Safari/other supported browsers use the requested native app, address
    field shortcut, query entry, submission, and current-screen verification.
- Added the shared `app.find_entity` executor tool. It returns exact visible
  accessibility matches, reports multiple matches, and refuses to choose a
  similar contact.
- The planner now receives the goal contract and cannot treat opening an app
  as completion for a compound goal.
- WhatsApp message planning now requires:
  1. WhatsApp open/focused
  2. contact/search observation
  3. exact entity resolution
  4. conversation opening
  5. message text, or a user question when missing
  6. typing verification
  7. confirmation before Send
  8. sent-state verification
- Non-secure accessibility/browser text entry remains pre-send and is guarded
  against credentials/secure fields; the final Send/click action retains the
  existing confirmation requirement.
- Added bounded app-focus recovery, explicit requested-app mismatch errors,
  current-screen re-observation, and structured failure metadata containing the
  current step, requested/resolved application, tool, retries, screen state,
  and final reason without raw private screen text.
- Goal plans and real task events are now available to the live task timeline.

### Mocked / automated verification

- Added computer-intelligence regression coverage for:
  - Safari versus Chrome targeting.
  - Safari multi-step search.
  - WhatsApp contact/entity extraction.
  - Missing message content.
  - Multiple entity matches and disambiguation.
  - Exact shared-executor entity matches.
  - WhatsApp type/confirm/send/verify semantics.
  - Application mismatch rejection.
  - Bounded recovery and focus behavior.
  - Stop during planning/multi-step execution.
  - Existing pause/continue and confirmation coverage.
  - Completion only after the compound success condition.
- Full backend suite: **332 tests passed**.
- Frontend suite: **4 tests passed**.
- Frontend typecheck and production build passed.
- Python compilation passed.

### Packaged verification

- Native Swift helper typecheck passed, with only the existing macOS API
  deprecation warning for `activateIgnoringOtherApps`.
- Tauri packaging passed after the bundle-aware native helper change.
- App signature validation passed.
- DMG verification passed.
- Packaged runtime smoke passed, including authenticated HTTP/WebSocket,
  simulated computer task execution, persistence, bundled STT discovery, and
  global stop.

### Physically verified

- No new physical Safari, Chrome, WhatsApp, Telegram, Finder, or Terminal
  acceptance has been performed in this session.
- No real WhatsApp contact search or message send has been claimed.
- No final-send confirmation has been bypassed.
- Existing native packaging, helper, local voice, and prior microphone checks
  remain valid as recorded above.

### Remaining limitations

- WhatsApp and Telegram semantic controls depend on the app’s current
  accessibility tree and still require a real Mac session with Accessibility
  permission for acceptance.
- Safari native search verification depends on accessible/screen-visible result
  content; a changed Safari UI may require another bounded re-plan.
- Browser/API connectors and native UI acceptance remain separate from this
  computer-use milestone.
- The required physical acceptance sequence remains:
  `Open Safari`, `Open Safari and search for AI tools`, `Open WhatsApp and find
  Thinkernest`, and `Open WhatsApp and message Thinkernest saying hello` with
  confirmation before Send.

## Packaged connection and Safari acceptance — 2026-10-06

### Implemented

- Packaged Tauri startup now uses the fixed local backend URL
  `http://127.0.0.1:8742`; it no longer selects a random ephemeral port.
- Frontend connection diagnostics now track HTTP health and WebSocket readiness
  separately, show `Connecting...` during bounded reconnects, retain close
  code/reason and retry count, and ignore stale socket generations.
- Native computer-permission reporting keeps helper Accessibility/Screen
  Recording state separate from NOVA settings, browser/filesystem access and
  microphone state.
- Added the sanitized `scripts/connection_diagnostic.py` HTTP/WebSocket probe.
- macOS system applications installed through the Cryptex path are now accepted
  by the guarded application launcher. Downloaded or untrusted `.app` paths
  remain blocked. Added regression coverage for both cases.

### Verified

- Full backend suite: **334 tests passed**.
- Frontend suite: **4 tests passed**; typecheck and production build passed.
- Rust packaged fixed-port test passed.
- Swift native helper typecheck passed with the existing
  `activateIgnoringOtherApps` deprecation warning.
- Rebuilt `/Users/parvez/NOVA/src-tauri/target/release/bundle/macos/NOVA.app`
  and `/Users/parvez/NOVA/src-tauri/target/release/bundle/dmg/NOVA_0.2.0_aarch64.dmg`.
- App signature verification and DMG verification passed.
- The actual packaged app reached `ready` at `127.0.0.1:8742`; sanitized
  native UI status reported `connected: true`.
- Authenticated packaged HTTP health returned 200 and authenticated `/ws`
  returned `system.ready`.
- A real, non-simulated packaged `Open Safari` task completed and verified:
  `Verified: Opening Safari.`

### Current physical state

- Packaged microphone availability and speech readiness reported true in the
  earlier connection milestone; the later direct native voice status recorded
  speech authorization value `0`, so current wake readiness is recorded in the
  newer spoken-pipeline section below.
- The current run reports NOVA Accessibility and Screen Recording settings as
  disabled; browser/filesystem settings report enabled. Physical permission
  grants and the remaining Safari search/WhatsApp acceptance sequence still
  require the user's production Mac session.

## Tauri event-name and wake lifecycle fix — 2026-10-07

### Exact cause

- `frontend/src/voice/wake.ts` registered the literal `native.voice` through
  `nativeListen`, which forwarded it unchanged to Tauri's `listen` command.
  `frontend/src/voice/nativeMicrophone.ts` used the same invalid event.
- The dot is not allowed in a Tauri event identifier. A native IPC regression
  test with the original `native.voice` value reproduces the exact
  `invalid args \`event\` for command \`listen\`` serialization error.
- Other native channels also used invalid dotted identifiers:
  `native.hotkey`, `native.control`, `native.mode`, `native.panel`, and
  `native.runtime`. Rust emitted those same names and discarded emit errors.
- No transcript, user command, UI label, or model output was used as a Tauri
  event name. `WAKE STARTING`, `MIC OFF`, and `Ready when you are` are display
  strings only. Dotted WebSocket event types and native voice JSON payload
  types are separate from Tauri event identifiers.
- Wake startup set `STARTING` before registration; rejection escaped to an App
  handler that rendered `String(error)`. Some native failures were also followed
  by `stop()`, overwriting `UNAVAILABLE` with `OFF`.

### Implemented

- Single shared event-name source:
  `frontend/src/services/nativeEvents.json`, consumed by TypeScript and Rust.
  The static names are `native:voice`, `native:hotkey`, `native:control`,
  `native:mode`, `native:panel`, and `native:runtime`.
- `nativeEvents.ts` validates non-empty strings and allowed characters, requires
  contract membership, and rejects misuse before calling Tauri. No runtime
  string transformation is performed. Optional registration diagnostics output
  only static event identifiers; they are disabled during ordinary operation.
- Updated every native frontend subscription and Rust emission to the shared
  contract. Backend/model/router/planner/connector/permission-policy source was
  not changed for this milestone.
- Wake initialization awaits listener registration before starting the native
  engine and awaits native `ready` before entering passive listening. A
  30-second readiness timeout and clean `Wake word unavailable` failure path
  prevent raw Tauri errors and stuck startup.
- Serialized startup/teardown across provider instances, guarded stale callbacks,
  cancelled pending readiness on unmount, and removed late subscriptions.
  Duplicate starts are idempotent. Reconnect/reinitialization waits for cleanup.
- Native follow-up/passive transitions emit readiness so frontend state reflects
  native events. Disabling wake resets its UI state to `OFF`.
- Microphone subscription failures now report a clean capture error, and late
  cancelled registrations clean up their own listener.
- Sanitized native UI status now includes the bounded internal `wake_state`.
- Added the opt-in `--event-smoke` packaged subscription probe and
  `scripts/native_event_smoke.py`. It subscribes to all six channels, exercises
  voice-channel native delivery, unsubscribes, checks delivery stops, and repeats
  registration without enabling the microphone.

### Verification

- **334 backend + 24 frontend + 4 Rust tests passed: 362 total.**
- Added 20 focused frontend tests and 3 native regression tests covering valid
  names, invalid-name rejection before IPC, UI/transcript exclusion, the shared
  wake channel, readiness, clean failures, timeout, native-driven states,
  cleanup, late registration, replacement providers, duplicate-free restart,
  microphone listener safety, original-error reproduction and native listen IPC.
- Frontend typecheck and production build passed.
- Native voice Swift typecheck passed.
- `./scripts/build_desktop.sh` rebuilt the app and DMG successfully.
- Strict app signature verification and DMG verification passed.
- Packaged event subscription smoke passed two registration/cleanup cycles with
  `voice_event: native:voice` and `passed: true`.
- Existing packaged backend smoke also passed authenticated HTTP/WebSocket,
  simulated task execution, bundled STT, persistence and global-stop checks.
- Launched the rebuilt
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/macos/NOVA.app`.
  Runtime reported `ready`, zero restarts, and frontend `connected: true`.
  Native UI wake state initially advanced beyond `STARTING` to `LISTENING`.

### Physical acceptance: partial, end-to-end task test not passed

- User reported **wake works, command fails**, then clarified that a task starts
  and fails. The exact task failure message was no longer visible.
- This is user-observed wake detection, not agent-performed spoken acceptance of
  both phrases. The complete `Hey Nova` / `Hello Nova` → spoken `Open Safari` →
  successful task sequence is **not verified**.
- A later native UI status read reported `wake_state: UNAVAILABLE`; the UI now
  maps this to a clean unavailable status. Its exact runtime cause and the task
  failure cause were not established from the available evidence.
- Next acceptance step: retain the failed spoken task's ID and exact error,
  verify both wake phrases, and confirm a spoken `Open Safari` task completes.
  Do not infer a planner or permission-policy defect from the missing message.

## Spoken command pipeline trace and packaged self-test — 2026-10-07

### Exact pipeline findings

- The prior physical task's exact error could not be recovered because its task
  panel message was no longer visible. New task metadata now retains a sanitized
  `pipeline_trace` and final failure record under the same task ID.
- Focused text/voice comparison found the first concrete divergence at the
  finalized STT handoff: text `Open Safari` became goal `Open Safari`, while
  voice `Hey Nova, open Safari` became `open Safari`. The voice boundary now
  removes only a leading `Hey Nova`/`Hello Nova`/`Nova` phrase, removes terminal
  sentence punctuation, and normalizes the first command character to produce
  the same readable command form. It does not title-case the remaining words.
- Wake-only finalized speech now emits `voice.wake_only` and exits before
  `save_user`, agent dispatch, or computer task creation. Empty/partial input
  remains rejected, and duplicate finalized audio cannot create a second task.
- Every computer task now carries a stable task ID from its first queued event
  through intent, planner result, selected tool, attempted action, result,
  verification, retry count, permission snapshot, screen state, provider/model,
  and sanitized final error. The trace is persisted in task history and shown in
  the debug panel with HEARD/INTENT/TASK/STEP/APP/TOOL/RESULT fields.
- A real packaged text task and a real packaged generated-speech task both
  reached the same normalized goal and completed successfully.

### Direct computer-use handoff finding and fix

- The packaged multi-step speech test reached Safari, then the planner emitted
  `browser.type` with Safari's native address-bar element ID but omitted its
  optional `application` field. The executor defaulted the missing field to
  Google Chrome and returned the misleading:
  `REQUESTED_APP_MISMATCH: The user requested Safari; browser action targeted Google Chrome.`
- Browser actions now inherit the explicit target application from the task
  intent when the planner omits that optional field. This is a bounded executor
  handoff correction; model routing and planner architecture were not changed.
- `BrowserProvider.type` now uses the native Safari address-bar path for
  non-Chrome targets and returns the real native permission error when native
  keyboard input is unavailable. Its verification path accepts the native
  typed result and active requested application.
- Regression coverage verifies application inheritance and Safari native browser
  typing. The fix was run in the real packaged app.

### Automated and packaged verification

- Full backend suite: **341 passed**.
- Frontend suite: **24 passed**; typecheck and production build passed.
- Rust native suite: **4 passed**.
- Native voice Swift typecheck passed with the existing macOS deprecation
  warning.
- Packaged app and DMG rebuilt successfully; strict signature verification and
  DMG verification passed.
- Packaged backend smoke passed authenticated HTTP/WebSocket, bundled STT,
  persistence, simulation smoke and global stop.
- Packaged native event subscription smoke passed two registration, delivery,
  cleanup and re-registration cycles using `native:voice`.
- Packaged runtime launched at `http://127.0.0.1:8742`, reported `ready`, and
  reported frontend `connected: true`.
- Packaged generated-speech pipeline smoke passed with `simulation=false`:
  - wake-only transcript `Hey NOVA!` → no task;
  - text `Open Safari` → task `82a88a0f-3006-412f-b9fc-11f612eed896`,
    `COMPLETED`;
  - voice `Hey NOVA! Open Safari!` → normalized `Open Safari`, task
    `d328cf79-4462-41d9-91eb-5a6d1eb3ba28`, `COMPLETED`, verified Safari;
  - text/voice normalized-command equivalence: passed.

### Packaged multi-step result

- Voice input `Hey Nova, open Safari and search AI tools` was transcribed as
  `Hey NOVA! Open Safari and search AI tools.` and normalized to
  `Open Safari and search AI tools`.
- Task `49503548-304a-4384-ae45-c8936dcde02e` selected Safari correctly,
  opened/focused Safari and then selected `browser.type` with
  `application: Safari` after the fix.
- It stopped safely with the exact native error:
  `ACCESSIBILITY_PERMISSION_REQUIRED` — `Enable Accessibility for the NOVA native helper or its launching terminal.`
- Direct native status confirms: microphone permission authorized (`3`),
  speech authorization not yet granted (`0`), `apple_speech_available: true`,
  Accessibility false, Screen Recording false. NOVA settings currently report
  Accessibility and Screen Recording `DISABLED_IN_NOVA`; browser and filesystem
  access are enabled. No permission was bypassed or inferred.

### Acceptance matrix

| Test | Result | Task ID | Failure code | Notes |
|---|---|---|---|---|
| Wake-only generated speech | PASS | none | none | `voice.wake_only`; zero task events |
| Text `Open Safari` | PASS | `82a88a0f-3006-412f-b9fc-11f612eed896` | none | Packaged, non-simulated, verified |
| Spoken `Hey Nova, open Safari` | PASS | `d328cf79-4462-41d9-91eb-5a6d1eb3ba28` | none | Generated local speech through packaged STT boundary |
| Voice/text equivalence | PASS | above | none | Both normalized to `Open Safari` |
| Spoken multi-step Safari search | BLOCKED | `49503548-304a-4384-ae45-c8936dcde02e` | `ACCESSIBILITY_PERMISSION_REQUIRED` | Safari targeting fixed; native keyboard requires Accessibility |
| WhatsApp contact resolution | BLOCKED | — | `ACCESSIBILITY_PERMISSION_REQUIRED` | No physical attempt made without Accessibility |
| WhatsApp message preparation | BLOCKED | — | `ACCESSIBILITY_PERMISSION_REQUIRED` | No contact guessed; no message sent |
| Pause / Continue / Stop physical task | BLOCKED | — | `ACCESSIBILITY_PERMISSION_REQUIRED` | Automated coverage remains passing; no physical claim |

### Physical status

- The final packaged app's direct native status reports microphone authorized,
  but speech authorization value `0`; current packaged UI may therefore show
  `Wake word unavailable` rather than `LISTENING` until the user completes the
  macOS speech permission prompt.
- The user previously observed wake detection working, but no new physical
  microphone acceptance of both wake phrases was performed after this final
  build. The generated-audio test is packaged STT/task-path verification, not a
  physical microphone claim.
- Accessibility and Screen Recording remain genuine external/manual blockers for
  Safari search, WhatsApp, cursor/UI interaction, and physical pause/continue/
  stop acceptance. No test was marked physically passed on that basis.

## Final stabilization — 2026-10-07

### FINAL STABLE

- The current Offline report was traced to a stale `/Applications/NOVA.app`
  instance writing shared status with `base_url: http://127.0.0.1:50786`,
  `status: failed`, and `restarts: 5`. The final project artifact uses the fixed
  `http://127.0.0.1:8742`. The stale instance was stopped before final launch.
- `MIC OFF` was a misleading active-capture label, not a backend state. The UI
  now derives `MIC READY` from native microphone permission, voice/STT settings,
  and connected backend state. `MIC OFF` remains for disabled/unavailable state;
  it is independent of backend connectivity and wake state.
- Native UI status is reset to disconnected at startup and exit so stale
  `connected:true` data is not retained across app instances.

### VERIFIED AUTOMATED

- Final full suite, run once after fixes: **341 backend, 25 frontend, 4 Rust**.
- Typecheck, frontend build, Python compilation, and Swift typecheck passed.
- FREE_ONLY/ZERO_BUDGET model policy, safety controls, confirmation gates,
  pause/continue/stop coverage, Google/GitHub/Notion mocked executor paths, and
  existing computer-use regressions remained passing.

### VERIFIED PACKAGED

- Final app launched from the requested project artifact and reported runtime
  `ready`, `connected:true`, fixed port `8742`, microphone available, and wake
  `PASSIVE`.
- Authenticated health and WebSocket handshake passed.
- Packaged backend smoke passed.
- Packaged native event smoke passed two subscription/delivery/cleanup cycles.
- Signature verification and DMG verification passed.
- Generated-audio packaged pipeline passed with `simulation=false`:
  wake-only created no task; text and voice `Open Safari` each completed; task
  IDs and traces were retained; voice/text normalized intent matched.

### VERIFIED PHYSICAL

- No new physical microphone phrase acceptance is claimed in this final pass.
  Generated speech verifies the packaged STT/task boundary, not a physical mic.
- Prior user observation of wake detection remains recorded separately.

### BLOCKED

- Final multi-step packaged Safari task retained task
  `1eefeae1-6608-4b76-bbde-49dc4ff237e7` and failed with exact
  `ACCESSIBILITY_PERMISSION_REQUIRED`: `Enable Accessibility for the NOVA native
  helper or its launching terminal.` Safari targeting and task handoff were
  correct; native keyboard input was blocked.
- Direct native permission state reports Accessibility false and Screen Recording
  false. Speech permission may also require user completion on a fresh session.
- WhatsApp contact/message, physical Safari search, cursor/UI interaction, and
  physical pause/continue/stop remain blocked and were not faked.

### FINAL ARTIFACTS

- `/Users/parvez/NOVA/src-tauri/target/release/bundle/macos/NOVA.app`
- `/Users/parvez/NOVA/src-tauri/target/release/bundle/dmg/NOVA_0.2.0_aarch64.dmg`

## Product audit and queued computer control — 2026-10-07

### FIXED

- Computer tasks now use the existing `TaskManager` as a serialized FIFO-style
  execution gate. A second task remains active with `QUEUED` status and starts
  only after the current task releases the computer-control slot; planner,
  executor, verification, permission, and safety paths are unchanged.
- Global shutdown enters a draining state and rejects new work while existing
  tasks are being stopped, preventing a task from being added during restart
  or settings teardown.
- Owner lookup continues to find queued tasks so pause, resume, skip,
  confirmation, and stop controls address the correct task before it acquires
  the slot.
- The compact task HUD now explains that `QUEUED` means it is waiting for
  NOVA's computer-control slot.
- The intent gate now recognizes calendar meeting/event requests as computer
  goals so they reach the registered read-only connector planner path.

### NEW VERIFICATION

- Added `scripts/agent_benchmark.py`, a deterministic no-network benchmark with
  31 synthetic intent fixtures across computer actions, search, files,
  messaging, multilingual commands, connectors, calendar, and safety.
- Benchmark result: **PASS**; queue serialization, single-model-per-request
  routing, paid-model blocking under `FREE_ONLY`/`ZERO_BUDGET_MODE`, wake
  normalization, and the fixed terminal allowlist all passed. No raw fixture
  content is saved.
- Added regression coverage for serialized computer tasks. The focused runtime
  file passed **43 tests**; focused runtime/router coverage passed **55 tests**.

### FINAL REGRESSION

- Full backend suite: **342 passed**, one existing Starlette/httpx deprecation
  warning.
- Frontend: **25 passed**, typecheck and production build passed.
- Rust: **4 passed** with Cargo's project toolchain path.
- Swift native voice helper typecheck passed; Python compilation passed.

### FINAL PACKAGED CHECKS

- Rebuilt and signed the release app at
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/macos/NOVA.app`.
- Recreated and verified the DMG at
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/dmg/NOVA_0.2.0_aarch64.dmg`.
- Isolated packaged backend smoke passed authenticated HTTP/WebSocket,
  bundled STT discovery, simulation, persistence, and global stop.
- Packaged native event smoke passed two subscription, delivery, cleanup, and
  resubscription cycles using `native:voice`.
- Packaged generated-audio smoke passed wake-only suppression, non-simulated
  text and spoken `Open Safari`, task tracing, and normalized voice/text
  equivalence. This remains generated-audio verification, not physical
  microphone acceptance.
- Packaged supervisor recovery passed after a backend kill: task/message data
  remained intact (`messages: 97`, `memories: 0`, `tasks: 25`) and the native
  runtime returned to `ready` on port `8742`.

### REMAINING ACCEPTANCE BLOCKERS

- Accessibility and Screen Recording permissions remain unavailable for native
  Safari, WhatsApp, cursor/UI, and physical pause/continue/stop interaction.
- Physical microphone/wake acceptance after this build remains unclaimed;
  generated macOS speech is not a physical microphone test.
- Real-account Google/GitHub/Notion connector acceptance was not performed.

## FINAL PRODUCT STATUS — 2026-10-07

### PERMISSION STATUS

- **Implemented:** one stable production bundle identifier,
  `local.nova.desktop`, with an explicit deterministic ad-hoc designated
  requirement. The packaged helper is `local.nova.computer-access`; the voice
  owner is `local.nova.voice`.
- **Implemented:** microphone and speech requests now check authorization state
  first and call Apple's request APIs only for `NOT_DETERMINED`. Granted or
  denied states do not trigger a new request on startup.
- **Implemented:** normalized diagnostic model reports `GRANTED`, `DENIED`,
  `NOT_DETERMINED`, or `UNAVAILABLE` as appropriate, with independent owners:
  microphone/speech → `local.nova.voice`; accessibility/screen recording →
  `local.nova.computer-access`.
- **Packaged verified:** direct bundled voice status reports
  `MICROPHONE: GRANTED` and `SPEECH_RECOGNITION: NOT_DETERMINED`; these are
  independent states. Computer permissions currently report Accessibility and
  Screen Recording disabled in NOVA settings, not silently granted.
- **Implemented:** `permission_diagnostics` Tauri command and
  `scripts/production_diagnostic.py` expose sanitized active path, identities,
  helper path, signing mode, designated requirement, and permission owners.
- **Implemented:** `scripts/launch_production.sh` refuses to launch when a
  different NOVA.app instance is active, preventing the old `/Applications` or
  temporary-installer instance from silently owning the shared runtime.

### COMPANION STATUS

- **Implemented:** native pet mode is the default compact experience; the full
  panel remains secondary and native onboarding no longer opens as a large
  first-launch panel.
- **Implemented:** lightweight 3D-cartoon robot treatment with body depth,
  visor/chest lighting, antenna, highlights, shadows, glow, breathing,
  listening/thinking/working/warning/success animations, and pointer-following
  eye parallax.
- **Implemented:** pet click opens the panel, double-click opens the full
  control mode, pointer drag invokes native window dragging, position persists,
  and Desktop settings provide lock-position control.
- **Implemented:** pet mode uses a transparent compact native window while the
  backend and companion lifecycle continue independently of panel visibility.
- **Packaged verified:** canonical release app launched at the requested path;
  active path diagnostics matched the project artifact.
- **Physical interaction status:** hover, click, drag, close-panel persistence,
  and visual animation acceptance were not manually verified in this session.

### VOICE STATUS

- **Implemented:** wake-only permission requests are state-aware and no longer
  conflate microphone authorization with speech recognition authorization.
- **Packaged verified:** generated-audio wake-only suppression, spoken `Open
  Safari`, normalization, task correlation, and voice/text equivalence passed.
- **Physical status:** physical microphone and “Hey Nova”/“Hello Nova” acceptance
  after this build remains unclaimed.

### MODEL STATUS

- **Preserved:** existing model registry, router, Ollama, Gemini, OpenRouter,
  free-only policy, zero-budget policy, capability selection, and bounded
  fallback.
- **Benchmark verified:** one primary model per request, local computer-capable
  selection, and paid-model blocking passed without unnecessary multi-model
  calls.

### COMPUTER-USE STATUS

- **Preserved:** goal → plan → observe → act → verify loop, explicit app
  targeting, visible cursor movement, permission gates, safety, confirmation,
  recovery, tracing, and shared executor.
- **Implemented:** serialized active/queued task execution and safe draining on
  shutdown.
- **Packaged verified:** non-simulated generated-audio Safari task path passed;
  physical native UI actions remain permission-blocked.

### TASK STATUS

- **Implemented:** queued task state is visible in the HUD; pause, continue,
  stop, confirmation, and task trace ownership remain correlated to queued and
  active task IDs.
- **Automated verified:** runtime queue regression passed and the complete
  backend suite passed.

### CONNECTOR STATUS

- **Preserved:** shared connector framework, Google/GitHub/Notion foundations,
  permissions, and read-only executor paths.
- **Automated verified:** existing mocked connector tests passed.
- **Physical/account status:** no real Google, GitHub, or Notion account task
  acceptance was performed.

### AUTOMATED TESTS

- Backend: **342 passed**, one existing Starlette/httpx deprecation warning.
- Frontend: **25 passed**; typecheck and production build passed.
- Rust: **4 passed**.
- Swift native voice helper typecheck passed.
- Python compilation passed.
- Agent benchmark: **40 cases, PASS**; no network and no raw fixture capture.

### PACKAGED TESTS

- App signature and explicit designated requirement verified:
  `local.nova.desktop`, ad-hoc signing, internal requirement present.
- DMG checksum verification passed.
- Authenticated packaged runtime smoke passed.
- Native event subscription/cleanup smoke passed for two cycles.
- Packaged generated-audio spoken pipeline passed after clearing stale orphan
  backends.
- Supervisor recovery passed with data preserved: 123 messages, 0 memories,
  29 tasks.

### PHYSICAL TESTS

- **Verified:** the canonical packaged app launched and was the active process;
  packaged backend reached `ready` on port `8742`.
- **Not claimed:** companion hover/click/drag, panel-close persistence,
  physical microphone/wake, physical Safari multi-step interaction, WhatsApp,
  and physical pause/continue/stop.

### BLOCKERS

- Accessibility is disabled in NOVA settings and macOS Screen Recording is
  unavailable for native UI interaction. These block physical Safari, WhatsApp,
  cursor, and pause/continue/stop acceptance.
- Speech Recognition remains `NOT_DETERMINED`; microphone is independently
  `GRANTED`. The user must complete the macOS speech prompt to enable Apple
  on-device wake recognition.
- No Apple Developer signing certificate is installed; release signing is
  deterministic ad-hoc rather than notarized Developer ID signing.

### KNOWN LIMITATIONS

- Force-killing a stale UI can leave its sidecar alive until the canonical
  launcher/diagnostic identifies and clears the conflict; normal Quit uses the
  native shutdown path. The production launcher now refuses ambiguous active
  identities instead of silently routing to the wrong app.
- Physical acceptance and real-account connector acceptance require the user’s
  macOS permissions and accounts; generated-audio and mocked tests do not count
  as those physical claims.

## Continuation completion — 2026-10-07

### COMPLETED

- Reconciled the interrupted checkpoint without repeating research or redesigning
  the companion, router, or computer-use architecture.
- Classified `Unauthorized: {"detail":"device_token_invalid"}` as external
  Astra/session authentication; NOVA has no matching application error and its
  authentication was not changed.
- Fixed exact-app verification to wait for the requested active application and
  require its resolved bundle ID. Fixed production diagnostics so shell command
  text cannot masquerade as a NOVA process, and fixed the canonical launcher to
  wait past stale status-file contents for fresh packaged readiness.

### AUTOMATED VERIFIED

- Backend: **345 passed**; frontend: **25 passed**; Rust: **4 passed**.
- Frontend typecheck/build, Python compilation, shell syntax, Swift typecheck,
  queue serialization, task tracing, model routing, `FREE_ONLY=true`, and
  `ZERO_BUDGET_MODE=true` passed.
- Agent benchmark: **40 cases, PASS**; one model per request and paid-model
  blocking remained enforced.

### PACKAGED VERIFIED

- Current artifacts rebuilt at approximately 12:35:
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/macos/NOVA.app` and
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/dmg/NOVA_0.2.0_aarch64.dmg`.
- Canonical launcher reached `ready` with zero restarts on fixed port `8742`;
  authenticated HTTP health and WebSocket readiness passed.
- Packaged smoke, native event subscription/cleanup, supervisor recovery with
  data preserved, bundled STT, generated wake-only/spoken task flow, app
  tracing, signature verification, and DMG verification passed.
- Final exact-app tasks passed with frontmost bundle IDs: Safari →
  `com.apple.Safari`, Google Chrome → `com.google.Chrome`, Finder →
  `com.apple.finder`.
- Companion window inspection found the independent compact NOVA window at
  280×330; the main panel remained optional/not frontmost during app tasks.

### PHYSICAL VERIFIED

- The canonical project artifact was the active packaged process throughout the
  final runtime checks; no `/Applications/NOVA.app` instance owned the runtime.
- Native helper diagnostics currently report platform Accessibility and Screen
  Recording available; microphone is `GRANTED` and speech recognition is
  `NOT_DETERMINED`. Generated audio is not counted as physical microphone
  acceptance.

### BLOCKED

- Final packaged Safari search task `0c8e3783-ad75-4036-b30d-2c954636be9e`
  stopped at `browser.type` with `ACCESSIBILITY_PERMISSION_REQUIRED`; the
  requested and resolved application was Safari. The complex research task
  likewise stopped at the same guarded native-input boundary.
- NOVA computer settings currently keep Accessibility and Screen Recording
  access disabled independently of connectivity; no permission was bypassed.
- Physical microphone wake acceptance, speech authorization, WhatsApp/UI
  interaction, and real-account connector acceptance remain unclaimed.

### REMAINING

- User action is required to enable NOVA computer permissions and complete the
  macOS speech-recognition prompt before physical multi-step Safari, WhatsApp,
  cursor, pause/continue/stop, and both wake-phrase acceptance can be claimed.
- Real Google/GitHub/Notion account acceptance and Developer ID signing remain
  outside this automated/package run.

## Continuation follow-up — 2026-10-07

### COMPLETED

- Reconciled the existing release-ready checkpoint; the Astra
  `device_token_invalid` response remains external session authentication and
  did not change NOVA authentication.
- Fixed packaged WhatsApp focus lookup to prefer its known bundle ID
  `net.whatsapp.WhatsApp`, avoiding the hidden Unicode marker in its localized
  display name.
- Fixed the canonical launcher to remove the previous `native-status.json`
  before opening NOVA, preventing a stale ready PID from being accepted for a
  new app instance.

### AUTOMATED VERIFIED

- Final regression after the fixes: **345 backend**, **25 frontend**, and
  **4 Rust** tests passed; frontend typecheck/build, Python compilation, shell
  checks, and Swift typechecks passed.
- Existing 40-case agent benchmark remains PASS with single-model routing,
  queue serialization, `FREE_ONLY=true`, and `ZERO_BUDGET_MODE=true`.

### PACKAGED VERIFIED

- Rebuilt current artifacts at approximately 12:58:
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/macos/NOVA.app` and
  `/Users/parvez/NOVA/src-tauri/target/release/bundle/dmg/NOVA_0.2.0_aarch64.dmg`.
- Canonical launcher reached fresh `ready` on `127.0.0.1:8742`; authenticated
  HTTP/WebSocket readiness, packaged smoke, native event cleanup, supervisor
  recovery, STT, voice pipeline, signature, and DMG verification passed.
- Native exact focus resolved Safari, Chrome, Finder, and WhatsApp to their
  expected bundle IDs. WhatsApp direct helper focus now succeeds.

### PHYSICALLY VERIFIED

- The active runtime is the canonical project artifact, not `/Applications/NOVA.app`.
- Native helper reports platform Accessibility and Screen Recording available;
  microphone is `GRANTED`; speech recognition is `NOT_DETERMINED`.
- Generated speech verifies the packaged voice/task boundary only; it is not a
  physical microphone acceptance claim.

### BLOCKED

- Safari search and the complex browser research task remain blocked at native
  input with `ACCESSIBILITY_PERMISSION_REQUIRED` because NOVA computer settings
  keep Accessibility and Screen Recording disabled.
- Physical wake phrases, companion hover/click/drag/panel acceptance, WhatsApp
  contact resolution, physical pause/continue/stop, and real connector account
  acceptance remain unclaimed.

### KNOWN LIMITATIONS

- The bare agent goal `Open WhatsApp` enters the existing WhatsApp
  contact-resolution workflow after its `app.open` step; the focused probe was
  cancelled before contact resolution and no contact was guessed or messaged.
- The existing macOS `activateIgnoringOtherApps` deprecation warning remains;
  it does not prevent bundle-correct focus verification.
