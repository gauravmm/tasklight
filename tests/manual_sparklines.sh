#!/usr/bin/env bash
# Manually exercise the /hook/claude-code sparkline path.
#
# Spawns a synthetic Claude Code session: writes a fake transcript JSONL
# and POSTs hook events to a running Tasklight on localhost:57017 every
# few seconds, ramping context_tokens upward. Hit ^C to stop; the
# script sends a SessionEnd before exiting.
#
# Optional: pass `reset` as the first arg to simulate a /clear-style
# context drop after ~30 s.
#
#   ./manual_sparklines.sh           # steady growth
#   ./manual_sparklines.sh reset     # growth, drop at ~30 s, growth
#   TASKLIGHT_HOST=192.168.1.5 ./manual_sparklines.sh

set -u

PORT=${TASKLIGHT_PORT:-57017}

# Resolve HOST. If TASKLIGHT_HOST is set, use it. Otherwise probe
# 127.0.0.1 first (Tasklight running locally / inside WSL), and fall
# back to the WSL2 default-route gateway (Tasklight on Windows host)
# only if localhost isn't listening.
probe() {
  curl -sf -m 1 -o /dev/null -X POST "http://$1:${PORT}/hook/claude-code" 2>/dev/null
}
if [ -n "${TASKLIGHT_HOST:-}" ]; then
  HOST=$TASKLIGHT_HOST
elif probe 127.0.0.1; then
  HOST=127.0.0.1
elif [ -n "${WSL_DISTRO_NAME:-}" ]; then
  WSL_HOST=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
  if [ -n "$WSL_HOST" ] && probe "$WSL_HOST"; then
    HOST=$WSL_HOST
  else
    HOST=127.0.0.1
  fi
else
  HOST=127.0.0.1
fi

ENDPOINT="http://${HOST}:${PORT}/hook/claude-code"
INTERVAL_S=${INTERVAL_S:-3}
DO_RESET=${1:-}

SESSION_ID="manual-$(date +%s)-$$"
WORKDIR=$(mktemp -d)
TRANSCRIPT="$WORKDIR/transcript.jsonl"
trap 'send_end; rm -rf "$WORKDIR"; exit 0' INT TERM

append_usage() {
  local input=$1 cache_creation=$2 cache_read=$3
  printf '{"type":"assistant","message":{"usage":{"input_tokens":%d,"cache_creation_input_tokens":%d,"cache_read_input_tokens":%d,"output_tokens":250}}}\n' \
    "$input" "$cache_creation" "$cache_read" >> "$TRANSCRIPT"
}

post_event() {
  local event=$1
  local hook_json
  hook_json=$(printf '{"session_id":"%s","cwd":"%s","hook_event_name":"%s","transcript_path":"%s"}' \
    "$SESSION_ID" "$WORKDIR" "$event" "$TRANSCRIPT")

  local hf tf
  hf=$(mktemp)
  tf=$(mktemp)
  printf '%s' "$hook_json" > "$hf"
  tail -n 20 "$TRANSCRIPT" 2>/dev/null > "$tf" || true

  curl -sf -m 1 \
    -H "X-Tasklight-Hostname: $(hostname)" \
    -F "hook=<$hf;type=application/json" \
    -F "transcript_tail=@$tf;type=text/plain" \
    "$ENDPOINT" \
    >/dev/null 2>&1 || echo "  (post failed; is Tasklight running on ${HOST}:${PORT}?)"

  rm -f "$hf" "$tf"
}

send_end() {
  echo
  echo "[manual_sparklines] sending SessionEnd for $SESSION_ID"
  post_event SessionEnd
}

echo "[manual_sparklines] session_id=$SESSION_ID"
echo "[manual_sparklines] endpoint=$ENDPOINT"
echo "[manual_sparklines] reset_mode=${DO_RESET:-off}"
echo "[manual_sparklines] interval=${INTERVAL_S}s — ^C to stop"
echo

post_event SessionStart

input=2000
cache_read=15000
cache_creation=500
i=0
reset_done=0

while true; do
  i=$((i + 1))

  if [ "$DO_RESET" = "reset" ] && [ $reset_done -eq 0 ] && [ $i -ge 10 ]; then
    # Simulate a /clear: tokens drop hard. The next sample after this
    # should trigger the reset rule (>20% drop) and fire a reset edge.
    input=1500
    cache_read=2000
    cache_creation=200
    reset_done=1
    echo "[$i] (RESET) tokens drop"
  else
    # Growth: cache_read is the bulk (warm reads), cache_creation
    # bursts every few iterations (loading new context), input
    # climbs slowly (real conversation).
    cache_read=$((cache_read + 4000 + RANDOM % 3000))
    if [ $((i % 4)) -eq 0 ]; then
      # Periodic creation burst — 5x normal — to make the orange band visible.
      cache_creation=$((cache_creation + 6000 + RANDOM % 3000))
    else
      cache_creation=$((cache_creation + 200 + RANDOM % 300))
    fi
    input=$((input + 600 + RANDOM % 400))
  fi

  total=$((input + cache_read + cache_creation))
  append_usage "$input" "$cache_creation" "$cache_read"
  echo "[$i] context_tokens=$total (in=$input cache_r=$cache_read cache_c=$cache_creation)"

  # Alternate PreToolUse / PostToolUse to look like a working session.
  if [ $((i % 2)) -eq 1 ]; then
    post_event PreToolUse
  else
    post_event PostToolUse
  fi

  sleep "$INTERVAL_S"
done
