#!/usr/bin/env bash
# Tasklight Claude Code hook bridge.
#
# Forwards Claude Code's hook stdin JSON to Tasklight's
# /hook/claude-code endpoint along with a tail of the session
# transcript JSONL (so the server can extract message.usage). The
# server reads hook_event_name from the JSON, so this script is
# invoked identically for every Claude Code hook event.
#
# Env overrides:
#   TASKLIGHT_PORT  server port (default 57017)
#   TASKLIGHT_HOST  override host; otherwise WSL2 default-route or 127.0.0.1
#   TASKLIGHT_TAIL_LINES  transcript tail line count (default 20)

set -u

INPUT=$(cat)

IF=$(mktemp)
printf '%s' "$INPUT" > "$IF"

TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
TF=$(mktemp)
if [ -n "$TP" ] && [ -r "$TP" ]; then
  tail -n "${TASKLIGHT_TAIL_LINES:-20}" "$TP" > "$TF"
fi

# WSL2-NAT: hooks inside WSL reach the Windows host via the default route.
WSL_HOST=
if [ -n "${WSL_DISTRO_NAME:-}" ]; then
  WSL_HOST=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
fi

HOST=${TASKLIGHT_HOST:-${WSL_HOST:-127.0.0.1}}
PORT=${TASKLIGHT_PORT:-57017}

curl -sf -m 1 \
  -H "X-Tasklight-Hostname: $(hostname)" \
  -F "hook=<$IF;type=application/json" \
  -F "transcript_tail=@$TF;type=text/plain" \
  "http://${HOST}:${PORT}/hook/claude-code" \
  >/dev/null 2>&1 || true

rm -f "$IF" "$TF"
