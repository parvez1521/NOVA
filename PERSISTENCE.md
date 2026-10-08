# Persistent chat history and long-term memory

NOVA stores both systems locally in `backend/data/nova.db` using Python's `sqlite3`. They share a database, **not a meaning**: questions, jokes and temporary plans remain chat messages; only explicit notes and confidently stable facts become memory.

## Schema

Schema version 2 is initialized/migrated automatically at backend startup (`PRAGMA user_version`). Phase 4 introduced version 1; the additive Phase 5 migration adds task history.

| Table | Columns |
| --- | --- |
| `conversations` | `id TEXT PRIMARY KEY`, `title`, `created_at`, `updated_at`, `archived`, `metadata_json` |
| `messages` | `id TEXT PRIMARY KEY`, `conversation_id` (foreign key, cascade delete), `role`, `content`, `timestamp`, `provider`, `model`, `input_type`, `metadata_json` |
| `memories` | `id TEXT PRIMARY KEY`, `category`, `content`, `source`, `confidence`, `created_at`, `updated_at`, `last_used_at`, `is_active`, `metadata_json` |
| `settings` | `key TEXT PRIMARY KEY`, `value` (JSON), `updated_at` |
| `tasks` | `id TEXT PRIMARY KEY`, `goal`, `status`, `summary`, `mode`, `created_at`, `updated_at`, `metadata_json` |

IDs are UUIDs; timestamps are UTC ISO-8601 strings. Roles/input types, categories, confidence and boolean flags have database constraints. Indexes cover message conversation/timestamp, memory category/activity/content, conversation update/title, and a unique active canonical fact key. Optional external-content FTS5 indexes and triggers cover message content, conversation titles and memory content. LIKE fallback remains available on SQLite builds without FTS5.

SQLite uses WAL, foreign keys, short lock waits, a serialized worker-thread connection and nested atomic transactions. The database file is owner-only (`0600`), ignored by source control and never bundled into frontend assets. `DATABASE_PATH` can override the default; relative paths resolve against the project root.

Task history is separate from chats and long-term memories: short sanitized summaries and allowlisted status metadata, without screenshots/raw observations/file or clipboard readouts/reasoning. Requested sanitized readouts can remain in normal chat answers; task-summary readout sections are stripped on saving and startup. Computer settings persist as `computer_settings`. Interrupted tasks are cancelled on restart; confirmed Clear everything waits for task cleanup before removing their history. See [COMPUTER_USE.md](COMPUTER_USE.md).

## Request flow

```text
Text input / local Whisper transcript
  → ConversationManager: select conversation, restore bounded context
  → persist sanitized user message immediately
  → MemoryCommandParser / MemoryExtractor (local deterministic rules)
  → relevant MemoryRetriever context
  → existing Agent → existing provider router and streaming
  → persist one assembled assistant answer after successful generation
  → existing UI and SpeechQueue / cleaner / resolver / native voice
```

Explicit memory commands return through the same `llm.started`, `llm.token`, `llm.completed`, `agent.completed` lifecycle with `provider="local"`, `model="memory-command"`. They make no provider call. Ordinary requests retain the existing smart thinking, provider fallback, answer-only streaming and cancellation. No per-token database writes occur. A completed generation is saved even if the user subsequently stops speaker playback; cancelled/failed generation saves only its user message and status metadata.

The last selected nonarchived conversation is stored in settings. Startup restores it; if unavailable, NOVA selects the latest nonarchived chat or creates one. Each WebSocket agent restores a bounded recent context before requests. New Chat selects a fresh conversation without clearing memory. Open/rename/archive/restore/delete preserve the same conversation IDs. Titles are deterministic, capped at 60 characters, and manually editable.

Chat history remains readable when memories are forgotten. Memory command exchanges and turns associated with inactive/replaced/deleted memory records are excluded from replayed model context, helping prevent an old preference from being resurrected through recent history.

## Memory behavior

`MemoryCommandParser` recognizes prefixes such as “Nova, remember that…”, “don't forget that…”, “save this…”, “forget that…” and “remove … from memory…”. It also handles viewing saved facts and direct response-language preference questions locally.

- **Remember:** explicit confidence `1.0`; confirmation is “Yaad rakh liya.”
- **Automatic:** strong response preferences `0.9`; supported stable tools/projects/workflows typically `0.85`, goals `0.8`. Only candidates meeting the configured threshold persist. The UI shows a subtle “Memory updated” indicator.
- **Skip:** temporary deadlines, questions, jokes, speculative/conditional facts, casual opinions and secrets. When rules cannot confidently classify an automatic fact, it is not stored.
- **Deduplicate:** normalize case/punctuation, common wording and known concepts into canonical fact keys/values; repeated Hinglish phrasing reuses one active fact.
- **Conflict:** replace the active value for a canonical preference/project/profile slot, deactivate the prior record and retain its historical version.
- **Forget:** deactivate one clear matching active fact; ambiguous requests list up to five readable choices and require a selection. No database IDs appear in answers.
- **Clear:** “Forget everything you remember about me” asks for confirmation. Confirmation is scoped to the current WebSocket, expires after five minutes, and is cancelled by an unrelated request, conversation switch or disconnect. Only after confirmation are all memory records physically deleted; chats remain.
- **View:** “What do you remember about me?” gives a readable list, with up to 30 facts per answer and a pointer to Settings for larger collections.

Categories: `preference`, `profile`, `work`, `project`, `workflow`, `technical`, `communication`, `goal`.

### Retrieval

FTS5/keyword candidates are ranked by word overlap, meaningful category matches and confidence. Response-language/length preferences are relevant to every ordinary answer; unrelated interests are not automatically included. Retrieval returns only the configured top K active facts and updates `last_used_at`. The system prompt gets compact factual bullets, never confidence/IDs/database metadata. Saved communication preferences refine default reply style; current explicit user instructions remain authoritative. Direct “How should you reply to me?” questions have deterministic language-appropriate replies.

No embeddings, extra classifier model, title-generation call, memory API, vector database or network retrieval is used.

## Settings and interface

Startup defaults:

```dotenv
DATABASE_PATH=backend/data/nova.db
SAVE_CHAT_HISTORY=true
MEMORY_ENABLED=true
MEMORY_AUTO_EXTRACTION=true
MEMORY_MIN_CONFIDENCE=0.75
MEMORY_TOP_K=5
```

Saved Memory settings override startup defaults across restarts. Threshold range is `0.75–1.0`; top K is `1–20`. Disabling chat saving and disabling memory are independent. Existing Voice settings remain browser-local, with their existing backend defaults.

- **History:** compact panel grouped Today/Yesterday/Previous, local search, previews/times, open, rename, archive, restore and confirmed delete; paginated messages/conversations.
- **New Chat:** fresh short-term context, retained old chats and memories.
- **Memory settings:** grouped active facts, add/edit/delete, memory controls, JSON/Markdown downloads, and confirmed Clear all chats / Clear all memories / Clear everything. App configuration is preserved by clearing.
- **Cmd+Shift+D:** conversation ID/message count, retrieved count/IDs/scores, inserted/updated/deleted counts and database latency alongside existing voice/LLM diagnostics. No raw credentials or reasoning.
- **Restart:** existing WebSocket event delivery now reconnects with bounded backoff; the UI reloads active history after backend reconnection and frontend reload.

## API

Validated conversation routes:

```text
GET    /api/conversations?archived=false&limit=100&offset=0
GET    /api/conversations/search?q=Premiere
POST   /api/conversations                   {"title": "optional title"}
GET    /api/conversations/{id}
PATCH  /api/conversations/{id}              {"title": "new title"}
DELETE /api/conversations/{id}?confirmed=true
GET    /api/conversations/{id}/messages?limit=100&offset=0
POST   /api/conversations/{id}/archive
POST   /api/conversations/{id}/restore
```

Validated memory/settings/control routes:

```text
GET    /api/persistence
GET    /api/settings/memory
PUT    /api/settings/memory
GET    /api/memories?include_inactive=false&limit=200&offset=0
GET    /api/memories/search?q=Premiere&limit=5
POST   /api/memories                        {"content": "I use Premiere Pro", "category": "work"}
PATCH  /api/memories/{id}                   {"content": "updated fact", "category": "work"}
DELETE /api/memories/{id}?confirmed=true
POST   /api/memories/clear                  {"confirmed": true}
POST   /api/data/clear                      {"scope": "chats|memories|everything", "confirmed": true}
GET    /api/export/chats?format=json|markdown
GET    /api/export/memories?format=json|markdown
```

The WebSocket accepts additive `conversation.select` and optional `conversation_id` on existing text/voice requests. Existing event names remain intact; useful additional events are `conversation.updated`, `memory.created`, `memory.updated`, `memory.deleted`, `memory.confirmation`, and `memory.choices`.

## Privacy, export and failures

The persistence boundary strips tagged reasoning/private metadata and restricts stored metadata keys. Recognizable credential-bearing messages are stored only as `[Sensitive input omitted]`; they are also withheld from provider context. Memory rejects sensitive credential topics. Detection covers common API-token prefixes, named keys/passwords/tokens, auth headers, cookie headers, private-key blocks, JWTs, plausible card numbers and named OTP/verification/PIN/CVV codes. Titles and exports use the sanitized persisted values. Visible emoji/formatting remain chat content; the existing `SpeechTextCleaner` remains responsible for spoken cleanup.

Raw audio is never written to the database. Existing temporary Whisper files are removed as before. Providers receive only bounded recent conversation plus relevant active memory, never complete databases; exports download locally and do not upload. JSON exports include historical/inactive memory versions; Markdown labels inactive versions. Natural forget deactivates a fact; confirmed Settings deletion and clear physically remove selected rows. SQLite deletion is not a forensic secure-erasure operation.

Startup/schema/temporary lock failures degrade gracefully: health exposes availability, REST returns a clean persistence error, and WebSocket requests display a recoverable saving warning while the assistant remains usable. Writes can recover on later requests. No secret content is written to error logs. Tests use isolated temporary databases, never the user's database.

## Verification on 2026-10-05

- **192 backend tests and 4 frontend audio tests pass.** Python compilation, TypeScript typecheck, production build and launcher shell syntax checks pass.
- Automated coverage includes initialization/migration, schema/indexes, FTS/LIKE search, transactions/locks/concurrent duplicate inserts, lifecycle, messages/cancellation/failure, privacy, commands, extraction/relevance/conflicts, APIs/exports/settings, voice integration and close/reopen/application-restart persistence.
- Actual backend/launcher restart: backend PID **43130 → 43307**, with the same active conversation, **4 saved messages and 1 saved memory** intact. Later PID **45978 → 46117** preserved the original four-message chat and **5 active memories**. Both tests used real local Ollama and the SQLite file, not mocks.
- Final implementation restart: backend PID **50072 → 51207** preserved a freshly generated two-message chat, its active selection and a separately saved memory; backend health remained OK. The known temporary test memory was removed afterward, and the verification chat was archived. Launcher PID at completion: **51180**.
- Browser reload/reconnect, History search/reopen, New Chat with retained memory, conflict replacement, forget, automatic stable facts, temporary-chat exclusion, credentials rejection, memory edit/delete, export download, setting changes, clear confirmation, chat preservation, archive/reopen and emoji-safe native TTS passed.
- Real browser audio fixtures → Whisper → local remember/forget → SQLite → native Rishi TTS passed, saving both recognized transcript and final response. “Hinglish” alone was misheard as “English” by the synthetic fixture; the clearer “a mix of Hindi and English in your replies” transcribed correctly and canonicalized to Hinglish. The implementation preserves the recognized words rather than silently rewriting STT.
- Warm alternating benchmark, six samples each: median browser first-answer latency **78 ms with saving/memory disabled vs 80 ms enabled**; median provider TTFT **39.20 ms vs 38.72 ms**. Median enabled persistence/retrieval overhead **0.661 ms**; a relevant-memory sample measured **1.181 ms**. These are measurements, not guarantees.
- Native STOP with persistence active terminated/reaped playback in a measured **78 ms**.
- The user separately confirmed physical-microphone remember/forget commands, saved transcript/reply visibility in History, memory changes in Settings and spoken confirmations. All requested acceptance checks passed.

Phase 5's final updated-app restart on 2026-10-06 (**36692 → 37481**) separately preserved **93 task records**, **210 checked messages**, **1 existing memory record**, active-conversation selection and Computer settings. No memory was seeded or cleared for that check. The full backend suite now has **300 passing tests**; Phase 4's acceptance results above remain its historical checkpoint. Physical-microphone computer-task acceptance is still pending; see [COMPUTER_USE.md](COMPUTER_USE.md).

## File manifest

Created:

```text
backend/app/database/db.py
backend/app/database/models.py
backend/app/database/repository.py
backend/app/database/migrations.py
backend/app/database/privacy.py
backend/app/agent/conversations.py
backend/app/api/persistence.py
backend/app/memory/__init__.py
backend/app/memory/commands.py
backend/app/memory/extractor.py
backend/app/memory/manager.py
backend/app/memory/repository.py
backend/app/memory/retriever.py
backend/tests/conftest.py
backend/tests/test_database.py
backend/tests/test_chat_persistence.py
backend/tests/test_memory.py
backend/tests/test_persistent_memory_flow.py
backend/tests/test_persistence_resilience.py
frontend/src/types/persistence.ts
frontend/src/services/persistence.ts
frontend/src/hooks/usePersistence.ts
frontend/src/components/History/HistoryPanel.tsx
frontend/src/components/History/ChatThread.tsx
frontend/src/components/Settings/MemorySettings.tsx
PERSISTENCE.md
```

Modified:

```text
backend/app/database/__init__.py
backend/app/agent/agent.py
backend/app/agent/context.py
backend/app/core/config.py
backend/app/main.py
backend/app/voice/tts/language.py
backend/tests/test_health.py
frontend/src/App.tsx
frontend/src/components/MiniPanel/MiniPanel.tsx
frontend/src/components/Settings/VoiceSettings.tsx
frontend/src/hooks/useNovaConnection.ts
frontend/src/types/nova.ts
frontend/src/styles.css
.env
.env.example
README.md
ARCHITECTURE.md
TOOLS.md
```

The local SQLite runtime file/sidecars are ignored data. Browser acceptance scripts and synthetic audio fixtures live outside the project in the approved temporary directory; no browser automation dependency was added to NOVA.

## Limitations

- Deterministic extraction intentionally has limited phrasing/language coverage; arbitrary semantic conflicts and paraphrases are not guaranteed. Unknown automatic facts are skipped; explicit notes/manual editing cover other facts.
- Secret detection is conservative pattern recognition, not a universal secret classifier. SQLite is owner-only but unencrypted; no encryption/key-management system is introduced.
- Voice memory follows Whisper's actual transcript; short Hinglish names can be misheard. Physical-microphone memory-command acceptance passed separately from synthetic-fixture integration.
- Memory confirmations expire on reconnect; confirmation is not persisted. Interrupted generation is not resumed after restart.
- Settings and active-conversation selection are local single-user application state, not a multi-user authenticated service. Multiple tabs can select different request conversation IDs, while the latest selected conversation controls default restoration.
