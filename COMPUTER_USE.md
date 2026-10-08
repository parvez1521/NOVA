# Phase 5: task-oriented computer use

NOVA accepts a desktop goal through the existing text or local Whisper path, observes the actual computer, proposes one structured action at a time, independently checks permissions, executes, and verifies the result. The implementation and automated/real-machine checks are in place. **Final physical-microphone acceptance for computer goals is pending.**

## Enable and use

1. Start `./setup.sh --start` and open <http://localhost:5173>.
2. Open **Computer**. Enable Computer Use; leave **Simulation Mode** on for a dry run, or turn it off for real actions. Save settings.
3. Enable only the needed access switches. Browser and filesystem access default on; screen, accessibility and terminal access default off.
4. For native mouse/keyboard, grant Accessibility to the helper shown in the permission panel. For screenshots, grant Screen & System Audio Recording to the responsible application named by macOS. This development launch was attributed to **Terminal**; the helper also needs its own grant where macOS lists it. Refresh permissions after granting/restarting the launcher.
5. Enter or speak a goal. Follow the compact progress/approval panel. Pause, Continue, Stop, spoken controls, and Escape operate on the same task.

The helper is compiled and ad-hoc signed on demand under `backend/data/computer/NOVA Computer Access.app`. It uses AppKit/ApplicationServices, ScreenCaptureKit and Vision already supplied by macOS, with Xcode command-line tools. No additional Python GUI framework or browser package is installed. Rebuilds replace the binary atomically and verify its signature.

Examples:

- “Open Chrome.” / “Open YouTube.”
- “Create a folder called NOVA-Test on my Desktop.”
- “Copy Hello NOVA to my clipboard.”
- “Open TextEdit, create a new document, and type Hello from NOVA.”
- “Take a screenshot of the active window and tell me what is visible.”
- “Create a folder called NOVA-Demo on my Desktop, open Chrome, search for free AI video tools, read the first few results, create results.txt inside the folder, write the tool names and URLs into it, verify the file exists, and tell me when done.”

Mouse/keyboard tasks need stable foreground focus during a step. If focus or a target changes while planning or waiting for approval, NOVA observes/replans or stops safely. A task's final answer lists verified actions; failures and simulation are explicitly identified.

## Settings

Environment startup defaults:

```dotenv
COMPUTER_USE_ENABLED=false
COMPUTER_USE_SIMULATION=true
COMPUTER_AGENT_MODE=SEMI_AUTONOMOUS
COMPUTER_SCREEN_ACCESS=false
COMPUTER_ACCESSIBILITY_ACCESS=false
COMPUTER_BROWSER_ACCESS=true
COMPUTER_FILESYSTEM_ACCESS=true
COMPUTER_TERMINAL_ACCESS=false
COMPUTER_VISION_ENABLED=true
COMPUTER_REQUIRE_CONFIRMATION=false
COMPUTER_ALLOW_CLOUD_PLANNING=false
TASK_TIMEOUT_SECONDS=300
MAX_TASK_STEPS=24
```

Computer settings persist in SQLite as `computer_settings` and override startup defaults. The optional `local_vision_model` setting selects an **already-installed** vision-capable Ollama model; changing it never downloads a model.

| Mode | Behavior |
| --- | --- |
| `ASSISTED` | Confirm every action |
| `SEMI_AUTONOMOUS` | Safe actions proceed; risky actions ask |
| `AUTONOMOUS` | Safe actions proceed; risky actions still ask |

**Confirm every action** is an additional override. Turning it off never removes required risky-action approval. Simulation checks the same schemas, scope, capabilities and permissions, but performs no OS actions or screen observation. Its final answer starts `[SIMULATION] No real actions performed`.

## Architecture

```text
Text / local Whisper transcript
  → existing conversation persistence and memory-command gate
  → ComputerRuntime / TaskManager
  → fresh accessibility / dedicated-browser DOM / on-demand OCR observation
  → local TaskPlanner: one schema-constrained decision
  → TaskExecutor: schema → secret guard → scope → observed target → permission
  → native/application/browser/filesystem/clipboard tool
  → fresh observation + independent VerificationEngine readback
  → completion review against the whole goal, or bounded recovery
  → factual response + short local task history + existing SpeechQueue
```

The LLM proposes; it cannot call OS tools directly or approve itself. Task schemas constrain both the registered tool name and its specific arguments. Planner context includes a compact verified-action ledger, observed source links, the current goal, and user/executor feedback. Only enabled capabilities are advertised. The existing local provider supplies structured output with `think:false`, a 12,288-token context, and bounded planner/completion budgets. Ordinary chat streaming, thinking policy, routing/fallback and voice cleanup retain their existing paths.

Task statuses: `QUEUED`, `PLANNING`, `OBSERVING`, `ACTING`, `VERIFYING`, `WAITING_FOR_CONFIRMATION`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`.

### Observation and actions

- Accessibility exposes visible native elements and focused editable-field values/selection state; secure fields are masked. Secrets spanning multiple labels or label/value pairs are omitted from the entire observation. Known native login/security content blocks capture before image processing.
- Browser operations use fixed CDP scripts in a visible **dedicated Chrome profile**, `backend/data/computer/chrome-profile`. NOVA does not attach to the personal browser profile. Closed dedicated windows can be reopened.
- Browser tools support navigation, search, DOM reading, observed-element click/type, scrolling, back/forward/reload and text finding. Model-supplied JavaScript is not accepted; downloads are disabled.
- Screenshots are on demand, captured locally into private temporary directories, processed with Vision OCR, and removed even on failure. Active-window capture selects the largest visible eligible window, avoiding tiny auxiliary windows.
- Optional vision fallback is loopback-local only. Model-inferred coordinates are capped at confidence `0.79`, below the `0.80` automatic-action threshold.
- Native mouse coordinates must lie inside a confidently observed native element; DOM viewport coordinates cannot be passed as desktop coordinates. Native interactions recheck the expected foreground process ID.
- Keyboard shortcuts release modifiers, Unicode typing preserves surrogate pairs, and verification reads the **target editable field's actual value**. Matching placeholders, labels or text in another document cannot prove successful typing. Native clicks can be checked by changed focus/caret/UI state.
- File writes/copies create exclusively; moves use macOS exclusive rename. Existing destinations are inspected through guarded read-only recovery rather than overwritten. Deletion is a file unlink or empty-directory removal, never recursive deletion.
- Application launch is limited to installed application folders; document opening rejects scripts, executables and installers.

### Scope, approvals and recovery

The task initially receives named Desktop/Downloads roots; otherwise its file scope is empty. Paths resolve symlinks and block credential/private-configuration stores. Leaving the scope requires explicit approval; scope approval does not approve a risky operation. Placeholder usernames are rejected.

Browser clicks, typing, application quit, moves/renames, clipboard reads and terminal calls require approval. Deletion is high-risk confirmation in every mode. Credentials, OTP/2FA, payment/security fields and security challenges require the user; System Settings interaction and typing commands through Terminal or embedded terminal panes are blocked. Native keyboard/UI typing in browser applications is blocked; browser input/navigation uses the registered browser tools. The terminal tool accepts only fixed singleton read-only commands: `pwd`, `date`, `whoami`, `uname`, `sw_vers`—no shell, scripts, pipelines or arbitrary arguments.

Only idempotent reads/navigation/app focus may receive up to two safe retries. Consequential actions are not blindly repeated. Failed writes to existing files trigger read-only inspection. Invalid targets or unverified research drafts trigger bounded replanning; repeated/no-progress plans and task step/planning limits stop the task. Research saved as “first few” results needs at least three distinct observed URLs, with names and URL pairs rather than a one-link description dump.

A timeout monitor pauses long-running tasks with Continue/Stop, including while planning or waiting for approval. Continue does not implicitly confirm an action. User follow-ups replace pending instructions; explicit memory commands remain memory operations.

### Stop and voice

Stop cancels the task/planner, terminates native subprocesses, clears pending actions and speech, releases held keys/modifiers, and returns the pet to idle. Repeated Stop requests do not interrupt cleanup, which finishes before another desktop task is admitted. Global Escape uses a native listen-only event tap while a real task is active; in-app Escape is also supported. Completed OS changes are not rolled back.

Starting push-to-talk pauses an active task and stops progress speech while recording. Local Whisper commands `pause`, `continue`, `stop`, `skip that`, `yes`/`confirm`, and `no` share the text task runtime and approval boundary. Hindi/Hinglish control variants include `रुको`, `रुक जाओ`, `ruko`, `pause karo`, `continue karo`, `जारी रखो`, `हाँ` and `नहीं`. Typed/recognized follow-ups and their acknowledgements persist in chat history. The native local wake-word mode remains off until explicitly enabled; passive audio is not uploaded or persisted.

## History, APIs and diagnostics

SQLite schema version **2** adds `tasks(id,goal,status,summary,mode,created_at,updated_at,metadata_json)` with update/status indexes. It retains sanitized goals, summaries capped at 1,200 characters, and allowlisted metadata: simulation, verified-step count, error code and conversation ID. Task history does not retain screenshots, raw observations, file/clipboard readouts, planner reasoning or credentials. Requested sanitized screenshot descriptions and file/clipboard answers may appear in normal saved chat answers when chat saving is enabled. Readout sections are removed from old task summaries on startup. Interrupted tasks are cancelled on restart; no actions resume automatically. Confirmed **Clear everything** stops active tasks before clearing task rows, chats and memories.

```text
GET/PUT /api/computer
GET     /api/computer/permissions
POST    /api/computer/permissions/{accessibility|screen_recording}/request
GET     /api/computer/tools
GET     /api/computer/tasks
GET     /api/computer/tasks/{id}
POST    /api/computer/tasks/{id}/confirm     {"confirmed": true|false}
POST    /api/computer/tasks/{id}/pause
POST    /api/computer/tasks/{id}/resume
POST    /api/computer/tasks/{id}/stop
```

WebSocket controls: `task.pause`, `task.resume`, `task.confirm`, `task.stop`. Progress arrives as additive `task.updated` events with task status/goal/mode, step, action, observation, verification and retry data. Existing agent/LLM/voice events are retained. The owning connection controls its live task; local REST APIs are single-user application controls. At most one desktop task is active globally.

The compact Computer panel shows capabilities, modes, simulation, system permission status and recent tasks. The progress panel shows confirmation, Pause/Continue/Stop and maps to existing pet states. Cmd+Shift+D includes task/action/result/verification/retry, app/URL, observation source/age, duration and permission diagnostics.

## Verification on 2026-10-05–06

- **300 backend tests and 4 frontend audio tests pass.** Python compilation, frontend typecheck/production build, launcher syntax and startup/health checks pass. Swift helper compilation/signature verification passes; Accessibility and Screen Recording remain granted.
- Actual Chrome opening, YouTube URL/DOM navigation, Desktop `NOVA-Test` creation and clipboard `Hello NOVA` readback passed.
- The requested `NOVA-Demo/results.txt` was created during development, then safely inspected on reruns. It contains invideo AI, VEED and Adobe names with observed URLs. A fresh-file run created `~/Desktop/NOVA-Demo-Final-20261005/results.txt` with three actual name/URL pairs: invideo AI (`https://invideo.io`), Canva (`https://www.canva.com`), Adobe (`https://www.adobe.com`). File content was independently read back.
- Real active-window capture/OCR, cleanup, new TextEdit document creation, `Hello from NOVA` typing and observed-target mouse movement passed through the live task UI. Separate guarded native checks confirmed an actual mouse click by caret movement **15 → 0**, and exact Hindi/emoji readback across a Unicode chunk boundary.
- Real local Whisper fixtures recognized “Nova, pause,” “Nova, continue,” and “Nova. Stop.” Deletion waited for confirmation; pause/continue retained approval; denial and spoken stop cancelled. `NOVA-Test` remained intact.
- Global Escape cancelled a pending task while TextEdit was focused: measured samples **175–280 ms**. Task STOP terminated/reaped actual `say` playback in a **104 ms** sample. These are measurements, not guarantees.
- Actual backend restart **26182 → 29009** retained **87 task records**, the verified fresh research task, the active conversation and **194 checked messages**, and Computer settings. Permissions remained granted. The memory list was empty before/after this check; Phase 4's separate positive-memory restart evidence remains documented in `PERSISTENCE.md`.
- Final updated-app restart **36692 → 37481** preserved **93 task records**, **210 checked messages**, **1 memory record**, the active conversation, Computer settings and the verified research task. Phase-5 health and native permissions passed after restart. Existing data was compared before/after; no memory was seeded or cleared for this check.
- Live simulation UI regression passed typed pause/continue with approval retained, completion after follow-up request IDs, combined `task.stop` + `agent.stop` returning the pet to idle, a subsequent original memory-command request, and reload/disconnect cancellation/reconnection without resuming. No browser errors occurred.
- Actual Chrome DOM fixture readback distinguished the input placeholder from its actual typed value and omitted password/OTP values. Native/browser typing tests reject matching labels with empty field values.
- Tests cover simulation nonexecution, schemas, permissions, no false completion, scope/symlinks, exclusive moves, script/installer blocks, cross-label/value secret masking, pre-capture security guards, embedded-terminal/browser input blocks, local-only vision, closed browser windows, timeout/pause/stop, repeated-stop cleanup/next-task admission, foreground changes during approval, voice/text integration, readout-free task history, task-history reopen and clear-everything cancellation.
- **Pending:** user confirmation of physical-microphone computer goals, spoken task pause/continue/stop, and perceived native progress speech. Synthetic Whisper fixtures are separate from physical-microphone acceptance.

### Remaining physical-microphone check

Open <http://localhost:5173>. In Computer settings enable Computer Use, turn Simulation off, keep semi-autonomous mode, and enable browser/filesystem access. In Voice settings enable speech input/output. Hold the microphone button while speaking each instruction, then release it:

1. “Nova, open YouTube.” Verify the actual YouTube page and sensible progress speech.
2. “Nova, create a folder called NOVA-Voice-Check on my Desktop.” Verify the actual folder.
3. Copy harmless text such as `Hello NOVA` to your clipboard, then say “Nova, read my clipboard.” While its approval is pending, say “Nova, pause,” then “Nova, continue.” The action must still require confirmation. Say “Nova, stop”; the task should cancel and the pet return to idle.

Confirm the recognized transcripts and task-control acknowledgements are visible in History, then report success or the exact failed step. Phase 5 remains pending until this user check passes.

## Main implementation files

`backend/app/computer/` contains contracts, native bridge/Swift helper, controllers, browser, observation/vision, planner, executor, scope/permissions, verification/retry, runtime/task manager and task history. `backend/app/tools/registry.py` and `safety.py` extend the original tool contract. Integration is in `backend/app/main.py`, `api/computer.py`, additive database migration/settings and persistence clear handling, and the structured provider methods.

Frontend additions are `types/computer.ts`, `services/computer.ts`, `components/Computer/TaskProgress.tsx`, `components/Settings/ComputerSettings.tsx`, and additive shell/event/style integration. Regression tests are `backend/tests/test_computer_{controllers,observation,planning,runtime,bridge}.py`. Live acceptance scripts and fixtures remain in the approved temporary directory; no Playwright dependency was added to NOVA.

## Current limits

Real native control is macOS-first and main-display-oriented. The packaged Tauri shell supplies bundled local runtime resources and global microphone shortcuts; the development shell remains browser-based with focused push-to-talk. A small local planner can ask for clarification or stop on unfamiliar UI, authentication, changing focus or complex goals; arbitrary applications/workflows are not guaranteed. The optional image-model fallback has mocked/contract coverage; native OCR is the verified screen path on this Mac. Task recovery never silently resumes after a restart or pretends a failed/denied step succeeded.
