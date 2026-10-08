# NOVA companion design note

## Principles

- **Peripheral first:** the robot is a small, non-activating, non-blocking
  desktop object; the control center is an on-demand workspace.
- **Attention by state:** idle motion is quiet and cheap; listening, planning,
  confirmation, completion, and errors create the visible attention moments.
- **Supervise without takeover:** a compact task bubble shows the current step,
  verified steps, pause/stop, and approval without expanding into a dashboard.
- **Keyboard and connector first:** use direct connectors or local fast routing
  before screen control; use screen control only when the requested app requires
  it and keep the user in control.
- **Original, efficient character:** layered geometry, light, parallax, and
  restrained spring motion create NOVA's dimensional robot without a 3D engine.
- **Goal contract over first action:** every task carries a bounded success
  condition and cannot finish until all meaningful parts are observed and
  verified.

## Product decisions

- Use two native concepts: a tight companion window and a separate optional
  control-center window.
- Default always-on-top is off; the companion may be pinned explicitly.
- Only the robot and its compact HUD receive pointer events in companion mode.
- Queue physical computer-control work, while safe connector work may remain
  independent when the executor and permissions allow it.
