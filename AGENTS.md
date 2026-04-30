# AGENTS.md

Guidance for coding agents working in this repository.

## Project Summary

Tasklight is a PyQt6 desktop overlay that displays live state for local AI coding agents. The app receives hook events over localhost HTTP, stores all state in memory, and renders a small always-on-top widget grouped by project directory.

The authoritative product/design notes are in [spec/DESIGN.md](spec/DESIGN.md).

## Core Commands

```bash
uv run python main.py
uv run python main.py --config PATH
uv run python -m py_compile main.py tasklight/*.py tasklight/overlay/*.py
bash tests/manual.sh
```

There is currently no formal automated test suite beyond basic compile checks and the manual hook driver.

## Architecture

High-level flow:

```text
hook GET or POST to /hook
  -> tasklight.server.HookServer
  -> tasklight.model.AgentStateModel.apply_event()
  -> overlay refresh/repaint
```

Key files:

- [main.py](main.py): thin CLI entry point
- [tasklight/app.py](tasklight/app.py): application composition root
- [tasklight/server.py](tasklight/server.py): localhost HTTP listener in a `QThread`
- [tasklight/model.py](tasklight/model.py): in-memory agent records and event application
- [tasklight/config.py](tasklight/config.py): config dataclasses, load/save, hot reload watcher
- [tasklight/tray.py](tasklight/tray.py): tray icon and context menu
- [tasklight/dialogs.py](tasklight/dialogs.py): small dialogs
- [tasklight/overlay/widget.py](tasklight/overlay/widget.py): Qt overlay widget, painting, interaction, docking
- [tasklight/overlay/view_model.py](tasklight/overlay/view_model.py): pure row-building logic
- [tasklight/overlay/layout.py](tasklight/overlay/layout.py): geometry, elision, hit testing
- [tasklight/overlay/presentation.py](tasklight/overlay/presentation.py): labels, glyphs, colors, elapsed formatting
- [tasklight/overlay/types.py](tasklight/overlay/types.py): overlay dataclasses

## Important Constraints

- PyQt GUI code must stay on the main thread.
- `HookServer` is the only intentional background thread boundary.
- Agent state is ephemeral and in memory only.
- The config watcher hot-reloads most UI settings; port changes still require restart.
- The app forces `QT_QPA_PLATFORM=xcb` at startup for X11/XWayland behavior.

## Working Norms

- Prefer small, typed helper modules over re-growing `main.py` or `widget.py`.
- Keep pure layout/presentation logic outside Qt event handlers where possible.
- When changing drag/docking behavior, be careful about:
  - global vs widget-local coordinates
  - screen selection on multi-monitor setups
  - `geometry()` vs `frameGeometry()`
  - preserving free-placement vs docked-placement behavior
- When changing config shape, update:
  - [tasklight/config.py](tasklight/config.py)
  - [spec/DESIGN.md](spec/DESIGN.md) if the user-facing behavior changed
  - [README.md](README.md) if setup or usage changed materially

## Manual Validation Checklist

When changing overlay behavior, validate as many of these as practical:

- app starts with `uv run python main.py`
- synthetic events render correctly via `bash tests/manual.sh`
- spinners update as expected
- approval rows tint correctly
- done rows can be dismissed
- group collapse/expand still works
- drag and docking behavior still works on at least one screen
- config hot reload still applies theme/dock changes correctly

## Hook Payload Reference

The server accepts both `GET /hook?param=value&...` (query params) and `POST /hook` (JSON body).

GET query params — used by Claude Code and Codex hooks:

| Param | Required | Notes |
|---|---|---|
| `source` | yes | e.g. `claude-code`, `codex` |
| `session_id` | yes | opaque stable identifier |
| `cwd` | yes | absolute project path |
| `event` | yes | see events below |
| `tool_name` | no | only for `tool_use` events |

POST JSON body — used by OpenCode plugin:

```json
{
  "source": "claude-code|opencode",
  "session_id": "...",
  "cwd": "/abs/path",
  "event": "...",
  "data": {}
}
```

Hook file dependencies: Claude Code hooks require `curl`; Codex hooks require `curl` and `jq`.

Supported events:

- `start`
- `thinking`
- `tool_use`
- `tool_result`
- `approval_required`
- `approval_granted`
- `stop`
- `exit`

## Documentation

- [README.md](README.md): user-facing overview and setup
- [CLAUDE.md](CLAUDE.md): Claude Code-specific repo notes
- [spec/DESIGN.md](spec/DESIGN.md): detailed design and intended behavior
