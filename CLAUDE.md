# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run python main.py              # run the app (default config: ./tasklight.yaml)
uv run python main.py --config PATH  # run with a specific config file
bash tests/manual.sh               # send synthetic hook events in a loop (requires app running)
```

There are no automated tests or linter configuration yet.

## Architecture

Tasklight is a PyQt6 desktop overlay that displays live AI agent state. The full design is in `spec/DESIGN.md`.

**Threading model:** The Qt GUI runs on the main thread. `HookServer` (a `QThread`) runs `http.server.HTTPServer` on `localhost:57017` and emits `event_received(dict)` signals back to the main thread — this is the only cross-thread boundary. All other code is single-threaded.

**Data flow:**

```
curl POST /hook
  → HookServer._Handler.do_POST()   [background thread]
  → HookServer.event_received signal
  → AgentStateModel.apply_event()   [main thread]
  → model signals (dataChanged / rowsInserted / rowsRemoved)
  → OverlayWidget._refresh() / update()
```

**Key files:**
- `main.py` — wiring: instantiates all components, builds the Qt app, owns the tray icon and `OverlayWidget`
- `tasklight/model.py` — `AgentRecord` dataclass + `AgentStateModel(QAbstractListModel)`; all state is in-memory, no persistence
- `tasklight/server.py` — `HookServer(QThread)`; responds 204 immediately, then emits signal
- `tasklight/config.py` — `AppConfig` dataclass hierarchy, YAML loader, `ConfigWatcher(QObject)` using `QFileSystemWatcher` for hot-reload

**Config hot-reload:** `ConfigWatcher` watches both the file and its parent directory (to catch atomic-save inode replacement). On change it emits `config_changed(AppConfig)`, wired to `OverlayWidget.apply_config()`. Port changes require restart; everything else is live.

**Platform:** `QT_QPA_PLATFORM=xcb` is forced at startup — Wayland lacks global screen coordinates (needed for menu positioning) and the system tray protocol.

**Hook payload schema:**
```json
{ "source": "claude-code|opencode", "session_id": "…", "cwd": "/abs/path", "event": "…", "data": {} }
```
Valid events: `start`, `thinking`, `tool_use` (data: `tool_name`), `tool_result`, `approval_required`, `approval_granted`, `stop`, `exit`.
