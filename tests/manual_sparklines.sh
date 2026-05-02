#!/usr/bin/env bash
# Manually exercise the /hook/claude-code sparkline path.
#
# Spawns three synthetic Claude Code sessions in parallel:
#   Session 1 (main)   — steady growth, auto-resets to ~40k at 200k tokens.
#   Session 2          — same project as session 1, slower growth.
#   Session 3          — different project; periodically asks for tool permission.
#
# Writes fake transcript JSONL files and POSTs hook events to a running
# Tasklight on localhost:57017.  Hit ^C to stop; the script sends
# SessionEnd for all sessions before exiting.
#
# Optional: pass `reset` as the first arg to also simulate a /clear-style
# context drop in session 1 after ~30 s.
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

TS=$(date +%s)
SESSION_ID="manual-${TS}-$$"
SESSION_ID_2="${SESSION_ID}-b"
SESSION_ID_3="${SESSION_ID}-c"

WORKDIR=$(mktemp -d)
WORKDIR_2=$(mktemp -d)
WORKDIR_3=$(mktemp -d)

TRANSCRIPT="$WORKDIR/transcript.jsonl"
TRANSCRIPT_2="$WORKDIR_2/transcript.jsonl"
TRANSCRIPT_3="$WORKDIR_3/transcript.jsonl"

# Sessions 1 and 2 share the same project directory; session 3 is a
# different project (the path need not exist on disk).
CWD_12="$WORKDIR"
CWD_3="/projects/other-project"

# ------------------------------------------------------------------
# Helpers (session-agnostic)
# ------------------------------------------------------------------

append_usage_to() {
  local transcript=$1 input=$2 cache_creation=$3 cache_read=$4
  printf '{"type":"assistant","message":{"model":"claude-opus-4-7","usage":{"input_tokens":%d,"cache_creation_input_tokens":%d,"cache_read_input_tokens":%d,"output_tokens":250}}}\n' \
    "$input" "$cache_creation" "$cache_read" >> "$transcript"
}

# Kept for the main session loop (backward-compatible wrapper).
append_usage() { append_usage_to "$TRANSCRIPT" "$@"; }

_post_event_raw() {
  local session_id=$1 cwd=$2 event=$3 transcript=$4
  local hook_json
  hook_json=$(printf '{"session_id":"%s","cwd":"%s","hook_event_name":"%s","transcript_path":"%s"}' \
    "$session_id" "$cwd" "$event" "$transcript")

  local hf tf
  hf=$(mktemp)
  tf=$(mktemp)
  printf '%s' "$hook_json" > "$hf"
  tail -n 20 "$transcript" 2>/dev/null > "$tf" || true

  curl -sf -m 1 \
    -H "X-Tasklight-Hostname: $(hostname)" \
    -F "hook=<$hf;type=application/json" \
    -F "transcript_tail=@$tf;type=text/plain" \
    "$ENDPOINT" \
    >/dev/null 2>&1 || echo "  (post failed; is Tasklight running on ${HOST}:${PORT}?)"

  rm -f "$hf" "$tf"
}

# Shorthand for the main session.
post_event() { _post_event_raw "$SESSION_ID" "$CWD_12" "$1" "$TRANSCRIPT"; }

# ------------------------------------------------------------------
# Cleanup: kill background loops, send SessionEnd for all sessions
# ------------------------------------------------------------------

BG_PIDS=()

cleanup() {
  for pid in "${BG_PIDS[@]+"${BG_PIDS[@]}"}"; do
    kill "$pid" 2>/dev/null || true
  done
  echo
  echo "[manual_sparklines] sending SessionEnd for all sessions"
  _post_event_raw "$SESSION_ID"   "$CWD_12" SessionEnd "$TRANSCRIPT"
  _post_event_raw "$SESSION_ID_2" "$CWD_12" SessionEnd "$TRANSCRIPT_2"
  _post_event_raw "$SESSION_ID_3" "$CWD_3"  SessionEnd "$TRANSCRIPT_3"
  rm -rf "$WORKDIR" "$WORKDIR_2" "$WORKDIR_3"
  exit 0
}
trap cleanup INT TERM

# ------------------------------------------------------------------
# Session 2: same project as session 1, slower / steadier growth
# ------------------------------------------------------------------
(
  sleep "$(echo "$INTERVAL_S" | awk '{printf "%.2f", $1/3}')"
  local_i=0
  local_input=1000 local_cache_read=8000 local_cache_creation=300

  _post_event_raw "$SESSION_ID_2" "$CWD_12" SessionStart "$TRANSCRIPT_2"
  while true; do
    local_i=$((local_i + 1))
    local_cache_read=$((local_cache_read + 2000 + RANDOM % 1500))
    if [ $((local_i % 5)) -eq 0 ]; then
      local_cache_creation=$((local_cache_creation + 4000 + RANDOM % 2000))
    else
      local_cache_creation=$((local_cache_creation + 100 + RANDOM % 200))
    fi
    local_input=$((local_input + 400 + RANDOM % 300))

    append_usage_to "$TRANSCRIPT_2" "$local_input" "$local_cache_creation" "$local_cache_read"

    if [ $((local_i % 2)) -eq 1 ]; then
      _post_event_raw "$SESSION_ID_2" "$CWD_12" PreToolUse "$TRANSCRIPT_2"
    else
      _post_event_raw "$SESSION_ID_2" "$CWD_12" PostToolUse "$TRANSCRIPT_2"
    fi
    sleep "$INTERVAL_S"
  done
) &
BG_PIDS+=($!)

# ------------------------------------------------------------------
# Session 3: different project; every 7 ticks ask for tool permission
# ------------------------------------------------------------------
(
  sleep "$(echo "$INTERVAL_S" | awk '{printf "%.2f", $1*2/3}')"
  local_i=0
  local_input=3000 local_cache_read=20000 local_cache_creation=800

  _post_event_raw "$SESSION_ID_3" "$CWD_3" SessionStart "$TRANSCRIPT_3"
  while true; do
    local_i=$((local_i + 1))
    local_cache_read=$((local_cache_read + 3000 + RANDOM % 2000))
    if [ $((local_i % 4)) -eq 0 ]; then
      local_cache_creation=$((local_cache_creation + 5000 + RANDOM % 2000))
    else
      local_cache_creation=$((local_cache_creation + 150 + RANDOM % 250))
    fi
    local_input=$((local_input + 500 + RANDOM % 350))

    append_usage_to "$TRANSCRIPT_3" "$local_input" "$local_cache_creation" "$local_cache_read"

    if [ $((local_i % 7)) -eq 0 ]; then
      # Simulate waiting for the user to approve a tool call.
      _post_event_raw "$SESSION_ID_3" "$CWD_3" PermissionRequest "$TRANSCRIPT_3"
      sleep $((INTERVAL_S * 2))
      _post_event_raw "$SESSION_ID_3" "$CWD_3" PostToolUse "$TRANSCRIPT_3"
    elif [ $((local_i % 2)) -eq 1 ]; then
      _post_event_raw "$SESSION_ID_3" "$CWD_3" PreToolUse "$TRANSCRIPT_3"
    else
      _post_event_raw "$SESSION_ID_3" "$CWD_3" PostToolUse "$TRANSCRIPT_3"
    fi
    sleep "$INTERVAL_S"
  done
) &
BG_PIDS+=($!)

# ------------------------------------------------------------------
# Main loop (session 1): growth with auto-reset at 200k
# ------------------------------------------------------------------

echo "[manual_sparklines] session_id=$SESSION_ID"
echo "[manual_sparklines] endpoint=$ENDPOINT"
echo "[manual_sparklines] reset_mode=${DO_RESET:-off}"
echo "[manual_sparklines] interval=${INTERVAL_S}s — ^C to stop"
echo "[manual_sparklines] parallel sessions: $SESSION_ID_2 (same project), $SESSION_ID_3 (other-project)"
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
    # Simulate a /clear: tokens drop hard.
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

  # Auto-reset to ~40k when the context window fills up (~200k).
  # The server will detect the >20% token drop and mark a reset edge on
  # the sparkline.
  if [ $total -ge 200000 ]; then
    prev_total=$total
    input=2000
    cache_read=30000
    cache_creation=8000
    total=$((input + cache_read + cache_creation))
    echo "[$i] (AUTO-RESET) was $prev_total → now $total (in=$input cache_r=$cache_read cache_c=$cache_creation)"
  else
    echo "[$i] context_tokens=$total (in=$input cache_r=$cache_read cache_c=$cache_creation)"
  fi

  append_usage "$input" "$cache_creation" "$cache_read"

  # Alternate PreToolUse / PostToolUse to look like a working session.
  if [ $((i % 2)) -eq 1 ]; then
    post_event PreToolUse
  else
    post_event PostToolUse
  fi

  sleep "$INTERVAL_S"
done
