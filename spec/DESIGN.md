# Tasklight – Design Specification

## 1. Overview

Tasklight is a lightweight, always-on-top desktop widget that displays the live state of AI coding agents (Claude Code, opencode) running on the local machine. Agents report their state via HTTP hooks to a local server. All state is ephemeral and held in memory.

---

## 2. Goals and Non-Goals

**Goals**

- Real-time display of per-agent state (thinking, tool use, waiting for approval, done)
- Dockable to any screen edge or corner
- Configurable appearance via hot-reloaded YAML
- Support Claude Code and opencode hook protocols
- Cross-platform: Windows and Linux
- Always-on-top natively on Windows

**Non-Goals**

- Persistence across restarts
- Remote/network agents
- Controlling or interacting with agents beyond dismissal

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Main Thread (Qt event loop)                             │
│                                                          │
│  ┌─────────────────┐    signals     ┌─────────────────┐  │
│  │  AgentStateModel │◄──────────────│  HookServer     │  │
│  │  (QAbstractList) │               │  (QThread)      │  │
│  └────────┬─────────┘               └─────────────────┘  │
│           │ model/view                                    │
│  ┌────────▼─────────┐    signals     ┌─────────────────┐  │
│  │  OverlayWidget   │◄──────────────│  ConfigWatcher  │  │
│  │  (DockableWindow)│               │  (QFileSystemW.)│  │
│  └──────────────────┘               └─────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**Component responsibilities:**

| Component | Role |
|---|---|
| `HookServer` | Receives POST events from agent hooks, validates, emits Qt signals |
| `AgentStateModel` | In-memory state for all agents; notifies views on change |
| `OverlayWidget` | Frameless dockable window; renders agent list |
| `ConfigWatcher` | Watches `config.yaml`; emits signal when file changes |
| `SpinnerDelegate` | Animated spinner rendered per-row via `QStyledItemDelegate` |
| `ClockTimer` | Single `QTimer` at 1 Hz driving elapsed-time label repaints |

---

## 4. Hook Integration

Agents report events by POSTing JSON to `http://127.0.0.1:{PORT}/hook`.

The port is fixed at a well-known default (e.g. `57017`) and configurable in `config.yaml`.

### 4.1 Payload Schema

```json
{
  "source":    "claude-code" | "opencode",
  "session_id": "<opaque string, unique per agent process>",
  "cwd":        "/absolute/path/to/project",
  "event":      "<event name>",
  "data":       { ... }
}
```

`session_id` is the stable key for tracking a single agent across events. `cwd` provides the display dirname.

### 4.2 Event Types

| `event` | Meaning | Next display state |
|---|---|---|
| `start` | Agent process launched | Thinking |
| `thinking` | Model is generating a response | Thinking |
| `tool_use` | Tool call started | Tool: `data.tool_name` |
| `tool_result` | Tool call finished | Thinking |
| `approval_required` | Awaiting user confirmation | Waiting for approval |
| `approval_granted` | User approved | Thinking |
| `stop` | Agent finished turn | Done |
| `exit` | Process exiting | *(remove after 30 s)* |

Unknown events are silently ignored.

### 4.3 Claude Code Hook Configuration

Add to `.claude/settings.json` (or the user-level settings):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "curl -sf -X POST http://127.0.0.1:57017/hook -H 'Content-Type: application/json' -d '{\"source\":\"claude-code\",\"session_id\":\"'\"$CLAUDE_SESSION_ID\"'\",\"cwd\":\"'\"$PWD\"'\",\"event\":\"tool_use\",\"data\":{\"tool_name\":\"'\"$CLAUDE_TOOL_NAME\"'\"}}' --max-time 1 || true"
          }
        ]
      }
    ],
    "PostToolUse": [ ... ],
    "Stop":        [ ... ],
    "Notification":[ ... ]
  }
}
```

Environment variables available in Claude Code hooks: `CLAUDE_SESSION_ID`, `CLAUDE_TOOL_NAME`, `CLAUDE_TOOL_INPUT`, `CLAUDE_TOOL_RESULT`.

For tools that pass structured JSON via stdin (e.g. `SubagentStop`), the hook command may pipe stdin to the POST body instead.

### 4.4 opencode Hook Configuration

Targeting opencode **1.14**. opencode fires hooks via its `hooks` config block. Because opencode does not expose a stable session identifier, the session ID is synthesised in the hook command as `opencode:{cwd}:{pid}` using `$$` (the shell PID of the hook process, which is stable for the lifetime of the agent).

The payload shape and URL are identical to Claude Code's, so the same server handles both.

### 4.5 Hook Reliability

Hooks must not block the agent and must not produce an error if Tasklight is not running. All curl calls suppress output and errors and tolerate server absence:

```sh
curl -sf --max-time 1 -X POST ... 2>/dev/null || true
```

- `-s` suppresses progress and error messages
- `--max-time 1` caps the total request time at 1 second
- `2>/dev/null` discards stderr (connection refused, etc.)
- `|| true` ensures the hook command always exits 0

The server responds immediately with `204 No Content`; all processing happens after the response is sent.

---

## 5. HTTP Server

PyQt6's GUI **must** run on the main thread. `HookServer` subclasses `QThread`, runs Python's stdlib `HTTPServer` inside `run()`, and emits a Qt signal for each validated request. Qt queues signal delivery across threads safely.

```python
class HookServer(QThread):
    event_received = pyqtSignal(dict)

    def run(self):
        server = HTTPServer(("127.0.0.1", PORT), _Handler)
        server.serve_forever()
```

The handler calls `self.server.owner.event_received.emit(payload)` from the background thread. Shutdown calls `server.shutdown()` from the main thread; `serve_forever` exits cleanly.

Hooks are fire-and-forget with small payloads, so the single-threaded request model is not a bottleneck. No extra dependencies.

---

## 6. State Model

All state lives in `AgentStateModel`, a `QAbstractListModel` subclass. No disk writes.

### 6.1 Per-Agent Record

```python
@dataclass
class AgentRecord:
    session_id: str
    source: str           # "claude-code" | "opencode"
    cwd: str
    dirname: str          # basename of cwd for display
    state: AgentState     # enum: THINKING | TOOL | APPROVAL | DONE
    tool_name: str | None # set during TOOL state
    state_entered_at: float   # time.monotonic() when state last changed
    started_at: float         # time.monotonic() at first event
    dismissed: bool       # True after click-to-dismiss
```

### 6.2 State Machine

```
          ┌──────────────────────────┐
          │          START           │
          └────────────┬─────────────┘
                       │ start / thinking
                       ▼
              ┌─────────────────┐
      ┌───────│    THINKING     │◄──────────────┐
      │       └────────┬────────┘               │
      │                │ tool_use               │ tool_result
      │                ▼                        │ approval_granted
      │       ┌─────────────────┐               │
      │       │      TOOL       │───────────────┤
      │       └─────────────────┘               │
      │                                         │
      │       ┌─────────────────┐               │
      │       │    APPROVAL     │───────────────┘
      │       └─────────────────┘
      │   approval_required ▲
      └─────────────────────┘
                       │ stop
                       ▼
              ┌─────────────────┐
              │      DONE       │──► dismissed on click
              └─────────────────┘
```

`exit` events remove the record entirely after a 30-second grace period (so the user sees DONE first).

### 6.3 Grouping

The view groups records by `dirname`. Groups with all records dismissed are hidden. Groups are sorted by the time of the most-recent active agent.

---

## 7. UI Layout

### 7.1 Window

- Frameless (`Qt.FramelessWindowHint`)
- Always-on-top (`Qt.WindowStaysOnTopHint`)
- Translucent background (`WA_TranslucentBackground`)
- Rounded corners via `paintEvent`

### 7.2 Per-Group Section

```
/dirname
 ◌  Thinking...                          03:31
 ◌  Tool: bash                        01:01:42
```

```
/dirname
 ◌  Thinking...                          03:31
 ●  Done                                 03:31    ← click to dismiss
```

```
/dirname
 ●  Waiting for approval                 03:31    ← yellow background row
```

- **Dirname header**: monospace, dimmed foreground; click to collapse/expand the group (see §7.3)
- **Spinner**: Unicode glyph cycling through frames; `claude-code` uses the Claude spinner and all other sources use braille; driven by a single `QTimer` at 125 ms (8 fps) ticking only when any `THINKING` or `TOOL` agents exist and animation is enabled
- **Status dot** (●): color-coded (green = DONE, red = APPROVAL)
- **Label**: state description; `Tool: <name>` during TOOL state
- **Elapsed timer**: right-aligned, shows time in current state; format `MM:SS` under 1 h, `HH:MM:SS` above; updated by a 1 Hz `QTimer`
- **APPROVAL row**: full-width yellow background tint behind the row

### 7.3 Collapsible Groups

Clicking the dirname header toggles the group between expanded (all rows visible) and collapsed (rows hidden, header only). Collapsed state is per-group and held in memory.

When collapsed, the header inherits the highest-priority status of any agent in the group:

| Priority | Condition | Header style |
|---|---|---|
| 1 (highest) | Any agent in `APPROVAL` | Red dot + `Waiting for approval` label + yellow background tint |
| 2 | Any agent in `DONE` | Green dot + `Done` label |
| 3 | All agents active | Spinner glyph + `Working…` label |

This ensures a collapsed group can never silently hide an approval request.

The collapsed header timer shows the minimum active row timer in the group, so the summary never overstates how long every agent has been in its current state.

### 7.4 Spinner Styles

Spinners are rendered as Unicode text glyphs, not `QPainter` arcs, so they work with any monospace font and require no image assets. A global frame counter increments on each 125 ms timer tick; each active row reads `frame % len(frames)` to pick its glyph.

Built-in styles:

| Name | Width | Frames | Frame count |
|---|---|---|---|
| `claude` | 1 char | `·` `✻` `✽` `✶` `✱` `✢` | 6 |
| `braille` | 1 char | `⠋` `⠙` `⠹` `⠸` `⠼` `⠴` `⠦` `⠧` `⠇` `⠏` | 10 |

`claude-code` rows use the `claude` frames. All other rows use `braille`.

If `theme.animate_spinners` is `false`, active rows render a single representative static glyph instead of cycling frames.

### 7.5 Sizing

The widget auto-sizes vertically to fit content. Width is fixed (configurable, default 320 px). Empty (no agents) collapses to zero height and hides.

### 7.6 Click Behavior

- Click on a DONE row → mark dismissed, remove from model, repaint
- Right-click anywhere → context menu (Quit, Preferences, About)
- Drag on the title/header area → reposition; snaps to nearest edge/corner on mouse release

---

## 8. Docking System

On mouse-release after a drag, the widget snaps to one of eight positions:

```
TL   TC   TR
ML         MR
BL   BC   BR
```

The snap target is determined by which quadrant the widget center falls in (with center-band deadzone for TC/BC/ML/MR). A configurable margin (default 16 px) separates the widget from the screen edge.

On multi-monitor setups, the widget snaps to the screen that contains its center point. `QScreen` geometry is used throughout; `availableGeometry()` respects taskbars.

Dock position is persisted in `config.yaml` (the only runtime write to config).

---

## 9. Configuration

### 9.1 `config.yaml` Schema

```yaml
port: 57017
dock:
  position: BR          # TL | TC | TR | ML | MR | BL | BC | BR
  margin: 16            # px from screen edge
  width: 320            # widget width in px

theme:
  background: "#1e1e1e"
  background_alpha: 0.85        # 0.0–1.0
  foreground: "#e8e8e8"
  dimmed: "#888888"
  animate_spinners: true
  accent_done: "#44cc77"
  accent_approval: "#ff4444"
  approval_row_bg: "#3a2800"
  font_family: "monospace"
  font_size_px: 13
  corner_radius: 10

timeouts:
  done_auto_remove_s: 0       # 0 = never auto-remove; else seconds after DONE
  exit_grace_s: 30
```

### 9.2 Hot Reload

`ConfigWatcher` uses `QFileSystemWatcher` to watch `config.yaml`. On change:

1. Parse YAML (catch errors; log and keep old config on failure)
2. Emit `config_changed(config: AppConfig)` signal
3. `OverlayWidget` re-applies theme and geometry without restart

Theme changes take effect immediately; port changes require restart (shown as a tray notification).

---

## 10. Platform Specifics

### Windows

- `Qt.WindowStaysOnTopHint` is sufficient for always-on-top behavior in normal use.
- Translucency via `WA_TranslucentBackground` + `DWMWA_EXTENDED_FRAME_BOUNDS` works on Windows 10+.
- The system tray icon uses the native Windows notification area.

### Linux

- `Qt.WindowStaysOnTopHint` works on most compositing WMs (KWin, Mutter, etc.).
- On tiling WMs (i3, Sway) always-on-top is WM-dependent; document as best-effort.
- Wayland: `QPA_PLATFORM=wayland` — floating panels may require `xdg-shell` layer-shell protocol. Use `QWindow::setFlag(Qt.WindowType.X11BypassWindowManagerHint)` only on X11 (detect via `QGuiApplication.platformName()`).
- File watching via `QFileSystemWatcher` uses `inotify` on Linux; no extra dependency.

---

## 11. Performance

- **Single repaint timer**: one `QTimer` at 125 ms drives spinner animation only when `THINKING` or `TOOL` agents are present and `theme.animate_spinners` is true; stops when all agents are DONE or gone.
- **Elapsed timer**: a separate 1 Hz `QTimer` repaints only the time-label column, not the full widget.
- **Model signals**: `AgentStateModel` emits fine-grained `dataChanged` with role masks so only the affected cell repaints.
- **No polling**: state is push-only from hooks. No background thread polls agents.
- **YAML parsing**: done in the watcher thread; only the parsed `AppConfig` dataclass crosses to the main thread via signal.
- **Spinner rendering**: Unicode text glyphs drawn with `QPainter.drawText`; no image assets. A global frame counter increments on each 125 ms tick; each row reads `frame % len(frames)` — no per-row animation state needed.

---

## 12. Project Layout

```
tasklight/
├── main.py                  # thin CLI entry point
├── config.yaml              # user config (hot-reloaded)
├── pyproject.toml
│
├── tasklight/
│   ├── __init__.py
│   ├── app.py               # QApplication composition root
│   ├── model.py             # AgentRecord, AgentState, AgentStateModel
│   ├── server.py            # HookServer (QThread + HTTP listener)
│   ├── config.py            # AppConfig dataclass, YAML loader, ConfigWatcher
│   ├── dialogs.py           # About + quit confirmation dialogs
│   ├── tray.py              # Tray icon and shared context menu
│   └── overlay/
│       ├── __init__.py
│       ├── widget.py        # OverlayWidget
│       ├── view_model.py    # Row construction and collapsed-group summaries
│       ├── layout.py        # Shared row geometry and hit testing
│       ├── presentation.py  # Glyph, color, and label policy
│       └── types.py         # Overlay row/layout dataclasses
│
└── spec/
    └── DESIGN.md
```

---

## 13. Open Questions

1. **opencode 1.14 hook schema** — exact env-var names need verification against the opencode 1.14 release. The synthesised session ID (`opencode:{cwd}:{pid}`) and payload shape are specified in §4.4; update once confirmed against the live installation.
