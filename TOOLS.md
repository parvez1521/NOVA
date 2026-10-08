# NOVA tools

Tools are backend plugins. A tool must provide:

- stable `name`
- human-readable `description`
- validated input schema
- `execute()` implementation
- explicit permission level
- structured result and error

## Permission levels

| Level | Meaning | Examples |
| --- | --- | --- |
| `safe` | Can run without confirmation | time, timer, open URL, search |
| `confirm` | Requires a visible user confirmation | typing, browser click, move/rename, clipboard read |
| `high_risk_confirm` | Destructive, external, or security-sensitive | delete, send, purchase, system configuration |

The LLM proposes a tool call, never grants its own permission. The executor validates the schema, scope, enabled capability and observed target, and asks for confirmation when required. New exclusive file creation is safe within task scope; overwrites are rejected. Assisted mode or Confirm every action adds approval even for safe tools. Task history stores short factual summaries rather than raw tool logs.

## Initial tool roadmap

1. `get_time`
2. `set_timer`
3. `open_application`
4. `open_url`
5. `clipboard_read`
6. `clipboard_write`
7. `take_screenshot`
8. `read_file`
9. `list_files`
10. `create_file`
11. `search_web`
12. `run_terminal_command`
13. `send_notification`

These original roadmap names are conceptual. The Phase 5 registry below supplies real macOS tools. Terminal access uses a fixed read-only allowlist, with no unrestricted shell or model-generated scripts.

## Phase 3 voice boundary

Spoken and typed commands use the same conversation entry point. Computer goals now route through the Phase 5 task runtime and guarded executor; conversation/memory requests retain their original path. App opening is reported only after actual verification.

The fixed whisper.cpp and `say` adapter processes are local infrastructure, not an unrestricted terminal tool. They use validated provider configuration, generated private temporary paths, no shell interpolation, and cancellable subprocess execution. Microphone audio is never a tool argument or sent to OpenRouter. Tool permissions remain authoritative regardless of whether a command was spoken or typed.

## Phase 5 executable registry

- Applications: `apps.list`, `app.active`, `app.open`, `app.focus`, `app.quit`.
- Mouse: `mouse.move`, `click`, `double_click`, `right_click`, `drag`, `scroll`; observed native coordinates only.
- Keyboard/accessibility: `keyboard.press`, `hotkey`, `type_text`, `key_down`, `key_up`; `ui.click`, `ui.type`.
- Files: `filesystem.list`, `read`, `create_folder`, `write`, `move`, `rename`, `copy`, `delete`, `open`.
- Clipboard: `clipboard.read`, `clipboard.write`.
- Browser: `browser.open`, `navigate`, `search`, `read`, `click`, `type`, `scroll`, `back`, `forward`, `reload`, `find_text`.
- Screen: `screen.capture`, processed temporarily/local-only.
- Terminal: `terminal.run`, disabled by default, accepting only singleton `pwd`, `date`, `whoami`, `uname`, `sw_vers`.

All schemas are generated from fixed controller signatures and validated independently. Downloads, credential typing, security-settings interaction, unsafe target coordinates, unknown tools/arguments and executable/script document opening are blocked. Native browser input and embedded-terminal typing cannot bypass registered browser/terminal tools. Risky/destructive/external actions remain confirmed in Autonomous mode. A successful tool acknowledgement alone is not goal completion: independent readback and whole-goal verification are required. Typing readback checks actual field values, not labels/placeholders. See [COMPUTER_USE.md](COMPUTER_USE.md).

TTS receives only cleaned answer text, not tool/event JSON, tagged private reasoning or fenced code. Installed voices are selected by answer language with locale-checked fallbacks; model output cannot inject `say` control sequences or choose an arbitrary command. Cleanup affects speech only, preserving the visible answer.

## Persistent chat and memory boundary

Memory commands are fixed local operations, not general tool execution. Text and Whisper transcripts share one SQLite conversation/memory layer. Remember/forget are parsed deterministically; unrelated chat is not automatically permanent memory. Clear-all memory requires confirmation, chat deletion/clearing is confirmed separately, and neither enables computer-control capabilities. Credential-bearing input/private reasoning is excluded by the persistence boundary. Local exports and settings do not upload the database; only bounded relevant context reaches a selected LLM provider.

## Connector boundary

Connectors are registered backend capabilities, separate from the agent and
computer executor. `/api/connectors` exposes status, permissions and declared
capabilities without secrets. OAuth/API tokens are stored through the macOS
Keychain boundary and never in SQLite or frontend state. The connector
foundation deliberately returns `UNAVAILABLE` for provider adapters that have
not been implemented; it never fabricates connected accounts or tools.

## Model routing boundary

`ModelRegistry` and `ProviderRouter` expose only sanitized model metadata.
Provider keys remain backend-only. Capability `null` means unknown and cannot
satisfy a required vision, audio, tool, or structured-output capability.
`FREE_ONLY` and `ZERO_BUDGET_MODE` are enforced before a cloud provider is
selected; unknown pricing is rejected. Computer planning requests the
structured-output/tool capability set and retains the deterministic local
planner preference unless cloud planning is explicitly enabled.
