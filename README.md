# <img src="spec/logo.png" height="32" /> Tasklight

Tasklight is a small always-on-top desktop overlay for watching local AI coding agents in real time.

It listens for hook events over HTTP and shows per-agent state such as:

- `Thinking`
- `Tool: <name>`
- `Waiting for approval`
- `Done`

Today the project targets Claude Code, Codex, and opencode hook payloads and renders them in a dockable PyQt6 desktop widget.

Tasklight is a tiny product that your agent can customize to your heart's desire. The full product/design notes live in [spec/DESIGN.md](spec/DESIGN.md).

## Features

- PyQt6 overlay with translucent background and always-on-top window
- Live in-memory agent state model
- Local HTTP hook server on `127.0.0.1`
- Grouping by project directory
- Collapsible groups
- Click-to-dismiss for done agents
- Drag-to-dock behavior with multi-screen-aware snapping
- Hot-reloaded YAML config
- System tray menu

## Install and run

### Windows — pre-built executable

Download `tasklight.exe` from the [Actions](../../actions) tab (pick the latest `windows-exe` artifact from a `v*` tag build) and run it directly — no Python required.

### Python — uvx (recommended)

TODO: One line command which downloads the latest release or `master` and runs that.

Or install it into a persistent tool environment:

```bash
uv tool install ./tasklight-*.whl
tasklight
```

## Hooks

TODO: Mention somewhere that `RemoteForward 57017` (or cli equivalent) will allow you to show this over remote forwards.

This repository includes ready-to-adapt hook files in [hooks/](hooks/) for:

- Claude Code
- Codex CLI
- OpenCode

Point your agent at them and they'll install it for you. They all forward lifecycle/tool events to Tasklight's local hook server.

## Configuration

Tasklight reads a YAML config file, defaulting to `./tasklight.yaml`.

If the file does not exist, Tasklight writes a default one on startup.

Current config shape:

```yaml
port: 57017

# Subnets allowed to send hook events. Hot-reloads without restart.
# Add e.g. 192.168.0.0/16 to accept hooks from LAN or a remote SSH forward.
allowed_subnets:
  - 127.0.0.0/8    # loopback
  - 172.16.0.0/12  # WSL2 host-guest NAT range

dock:
  position: BR
  margin: 16
  width: 360

theme:
  background: "#1e1e1e"
  background_alpha: 0.85
  foreground: "#e8e8e8"
  dirname_fg: "#888888"
  hostname_fg: "#5599cc"
  system_cursor: true
  animate_spinners: true
  done_fg: "#44cc77"
  done_bg: ""
  approval_fg: "#ff4444"
  approval_bg: "#a47000"
  font_family: "monospace"
  font_size: 13
  corner_radius: 10

timeouts:
  done_auto_remove_s: 0
  exit_grace_s: 30
```

Most config changes hot-reload automatically. Port changes require a restart.
