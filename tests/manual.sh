#!/usr/bin/env bash
# Manual test: simulates realistic claude-code and opencode agent sessions.
# Usage: bash tests/manual.sh [PORT]
# Runs forever; Ctrl-C to stop.

PORT=${1:-57017}
URL="http://127.0.0.1:$PORT/hook"

post() {
    curl -sf --max-time 1 -X POST "$URL" \
        -H "Content-Type: application/json" \
        -d "$1" 2>/dev/null || true
}

event() {
    local source=$1 session=$2 cwd=$3 event=$4 data=${5:-\{\}}
    post "{\"source\":\"$source\",\"session_id\":\"$session\",\"cwd\":\"$cwd\",\"event\":\"$event\",\"data\":$data}"
}

say() { echo "  [$1 / $(basename "$3")] $4"; }

TOOLS=("bash" "read_file" "write_file" "grep" "web_search" "python")

# Run one agent session to completion, then pause 5 s and repeat forever.
# Args: source  session_id  cwd
run_agent() {
    local source=$1 session=$2 cwd=$3
    local dir; dir=$(basename "$cwd")

    while true; do
        say "$source" "$session" "$cwd" "start"
        event "$source" "$session" "$cwd" start

        # 2–4 thinking+tool cycles per turn.
        local cycles=$(( RANDOM % 3 + 2 ))
        for (( i=0; i<cycles; i++ )); do
            say "$source" "$session" "$cwd" "thinking"
            event "$source" "$session" "$cwd" thinking
            sleep $(awk 'BEGIN{printf "%.1f", 1 + rand()*2}')

            # ~20% chance of approval required before a tool call.
            if (( RANDOM % 5 == 0 )); then
                say "$source" "$session" "$cwd" "approval_required"
                event "$source" "$session" "$cwd" approval_required
                sleep $(awk 'BEGIN{printf "%.1f", 3 + rand()*4}')
                say "$source" "$session" "$cwd" "approval_granted"
                event "$source" "$session" "$cwd" approval_granted
                sleep 0.3
            fi

            local tool="${TOOLS[$((RANDOM % ${#TOOLS[@]}))]}"
            say "$source" "$session" "$cwd" "tool_use: $tool"
            event "$source" "$session" "$cwd" tool_use "{\"tool_name\":\"$tool\"}"
            sleep $(awk 'BEGIN{printf "%.1f", 0.5 + rand()*3}')
            event "$source" "$session" "$cwd" tool_result
        done

        say "$source" "$session" "$cwd" "stop (done)"
        event "$source" "$session" "$cwd" stop

        echo "  [$source / $dir] waiting 5 s before next session…"
        sleep 5
    done
}

echo "Tasklight manual test — sending to $URL"
echo "Agents:"
echo "  claude-code  sess-cc-1  /home/user/projects/myapp"
echo "  claude-code  sess-cc-2  /home/user/projects/myapp   (same dir, parallel)"
echo "  opencode     sess-oc-1  /home/user/projects/backend"
echo ""

# Run all three agents in parallel, staggered by 2 s so their cycles interleave.
run_agent "claude-code" "sess-cc-1" "/home/user/projects/myapp"    &
sleep 2
run_agent "claude-code" "sess-cc-2" "/home/user/projects/myapp"    &
sleep 2
run_agent "opencode"    "sess-oc-1" "/home/user/projects/backend"  &

wait
