#!/usr/bin/env bash
# Manual test: sends a stream of synthetic hook events to the local server.
# Usage: bash tests/manual.sh [PORT]
# Runs forever; Ctrl-C to stop.

PORT=${1:-57017}
URL="http://127.0.0.1:$PORT/hook"

post() {
    curl -sf --max-time 1 -X POST "$URL" \
        -H "Content-Type: application/json" \
        -d "$1" 2>/dev/null || true
}

# Three synthetic agents across two directories.
AGENTS=(
    "claude-code|sess-aaa|/home/user/projects/myapp"
    "claude-code|sess-bbb|/home/user/projects/myapp"
    "opencode|sess-ccc|/home/user/projects/backend"
)

TOOLS=("bash" "read_file" "write_file" "grep" "web_search" "python")

agent_event() {
    local source session cwd event data
    IFS='|' read -r source session cwd <<< "$1"
    event=$2
    data=${3:-\{\}}
    post "{\"source\":\"$source\",\"session_id\":\"$session\",\"cwd\":\"$cwd\",\"event\":\"$event\",\"data\":$data}"
}

echo "Sending events to $URL — Ctrl-C to stop."

# Start all agents.
for agent in "${AGENTS[@]}"; do
    agent_event "$agent" "start"
done
sleep 0.5

# Main loop: cycle each agent through a realistic sequence.
while true; do
    for agent in "${AGENTS[@]}"; do
        # Thinking phase.
        agent_event "$agent" "thinking"
        sleep "$(awk 'BEGIN{printf "%.1f", 1+rand()*2}')"

        # Occasionally require approval instead of a tool.
        if (( RANDOM % 5 == 0 )); then
            agent_event "$agent" "approval_required"
            sleep "$(awk 'BEGIN{printf "%.1f", 2+rand()*3}')"
            agent_event "$agent" "approval_granted"
            continue
        fi

        # Tool use.
        tool="${TOOLS[$((RANDOM % ${#TOOLS[@]}))]}"
        agent_event "$agent" "tool_use" "{\"tool_name\":\"$tool\"}"
        sleep "$(awk 'BEGIN{printf "%.1f", 0.5+rand()*3}')"
        agent_event "$agent" "tool_result"

        # Occasionally finish the turn.
        if (( RANDOM % 4 == 0 )); then
            agent_event "$agent" "stop"
            sleep 3
            # Restart so the widget shows a new session.
            agent_event "$agent" "start"
        fi
    done
done
