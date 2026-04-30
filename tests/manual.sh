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
    local source=$1 session=$2 cwd=$3 ev=$4 data=${5:-\{\}} hn=${6:-}
    local hn_field=
    [ -n "$hn" ] && hn_field=",\"hostname\":\"$hn\""
    post "{\"source\":\"$source\",\"session_id\":\"$session\",\"cwd\":\"$cwd\",\"event\":\"$ev\",\"data\":$data$hn_field}"
}

say() { echo "  [${6:-local} / $1 / $(basename "$3")] $4"; }

TOOLS=("bash" "read_file" "write_file" "grep" "web_search" "python")

# Run one agent session to completion, then pause 5 s and repeat forever.
# Args: source  session_id  cwd  [hostname]
run_agent() {
    local source=$1 session=$2 cwd=$3 hn=${4:-}
    local dir; dir=$(basename "$cwd")

    while true; do
        say "$source" "$session" "$cwd" "start" "" "$hn"
        event "$source" "$session" "$cwd" start {} "$hn"

        # 2–4 thinking+tool cycles per turn.
        local cycles=$(( RANDOM % 3 + 2 ))
        for (( i=0; i<cycles; i++ )); do
            say "$source" "$session" "$cwd" "thinking" "" "$hn"
            event "$source" "$session" "$cwd" thinking {} "$hn"
            sleep $(awk 'BEGIN{printf "%.1f", 1 + rand()*2}')

            # ~20% chance of approval required before a tool call.
            if (( RANDOM % 5 == 0 )); then
                say "$source" "$session" "$cwd" "approval_required" "" "$hn"
                event "$source" "$session" "$cwd" approval_required {} "$hn"
                sleep $(awk 'BEGIN{printf "%.1f", 3 + rand()*4}')
                say "$source" "$session" "$cwd" "approval_granted" "" "$hn"
                event "$source" "$session" "$cwd" approval_granted {} "$hn"
                sleep 0.3
            fi

            local tool="${TOOLS[$((RANDOM % ${#TOOLS[@]}))]}"
            say "$source" "$session" "$cwd" "tool_use: $tool" "" "$hn"
            event "$source" "$session" "$cwd" tool_use "{\"tool_name\":\"$tool\"}" "$hn"
            sleep $(awk 'BEGIN{printf "%.1f", 0.5 + rand()*3}')
            event "$source" "$session" "$cwd" tool_result {} "$hn"
        done

        say "$source" "$session" "$cwd" "stop (done)" "" "$hn"
        event "$source" "$session" "$cwd" stop {} "$hn"

        echo "  [${hn:-local} / $source / $dir] waiting 5 s before next session…"
        sleep 5
    done
}

# ── Scenario selection ────────────────────────────────────────────────────────

SCENARIO=${2:-local}

case "$SCENARIO" in

local)
    # Three local agents (no hostname field): header should show plain /dirname.
    echo "Tasklight manual test — scenario: local (no hostname prefix expected)"
    echo "  claude-code  sess-cc-1  /home/user/projects/myapp"
    echo "  claude-code  sess-cc-2  /home/user/projects/myapp   (same dir, parallel)"
    echo "  opencode     sess-oc-1  /home/user/projects/backend"
    echo ""
    run_agent "claude-code" "sess-cc-1" "/home/user/projects/myapp"   &
    sleep 2
    run_agent "claude-code" "sess-cc-2" "/home/user/projects/myapp"   &
    sleep 2
    run_agent "opencode"    "sess-oc-1" "/home/user/projects/backend" &
    ;;

remote)
    # One local agent + one remote agent in the same dirname: hostname prefix
    # must appear on both groups (local shows as "local:", remote as "dev-server:").
    echo "Tasklight manual test — scenario: remote (hostname prefix expected on all groups)"
    echo "  claude-code  sess-local   /home/user/projects/myapp            (no hostname)"
    echo "  claude-code  sess-remote  /home/user/projects/myapp  dev-server (same dirname!)"
    echo "  opencode     sess-oc-1    /home/user/projects/backend           (no hostname)"
    echo ""
    run_agent "claude-code" "sess-local"   "/home/user/projects/myapp"            &
    sleep 2
    run_agent "claude-code" "sess-remote"  "/home/user/projects/myapp"  "dev-server" &
    sleep 2
    run_agent "opencode"    "sess-oc-1"    "/home/user/projects/backend"           &
    ;;

multi-host)
    # Three distinct hostnames across different dirs: sorted (hostname, dirname)
    # order should be: build-server:/api, build-server:/worker, dev-server:/myapp.
    echo "Tasklight manual test — scenario: multi-host (sorted by hostname then dirname)"
    echo "  claude-code  sess-1  /home/user/projects/myapp    dev-server"
    echo "  opencode     sess-2  /home/user/projects/api      build-server"
    echo "  claude-code  sess-3  /home/user/projects/worker   build-server"
    echo ""
    run_agent "claude-code" "sess-mh-1" "/home/user/projects/myapp"  "dev-server"   &
    sleep 2
    run_agent "opencode"    "sess-mh-2" "/home/user/projects/api"    "build-server" &
    sleep 2
    run_agent "claude-code" "sess-mh-3" "/home/user/projects/worker" "build-server" &
    ;;

hostname-exits)
    # Remote agent exits, leaving only local agents: hostname prefix should
    # disappear once the last remote record is gone.
    echo "Tasklight manual test — scenario: hostname-exits (prefix disappears after remote exits)"
    echo "  claude-code  sess-local   /home/user/projects/myapp            (loops forever)"
    echo "  claude-code  sess-remote  /home/user/projects/myapp  dev-server (exits after one turn)"
    echo ""
    run_agent "claude-code" "sess-local" "/home/user/projects/myapp" &

    # Run the remote agent for exactly one cycle then exit.
    sleep 1
    echo "  [dev-server / claude-code / myapp] start"
    event "claude-code" "sess-he-remote" "/home/user/projects/myapp" start {} "dev-server"
    sleep 3
    echo "  [dev-server / claude-code / myapp] stop"
    event "claude-code" "sess-he-remote" "/home/user/projects/myapp" stop {} "dev-server"
    sleep 4
    echo "  [dev-server / claude-code / myapp] exit — hostname prefix should now vanish"
    event "claude-code" "sess-he-remote" "/home/user/projects/myapp" exit {} "dev-server"
    ;;

*)
    echo "Usage: bash tests/manual.sh [PORT] [SCENARIO]"
    echo "Scenarios: local (default)  remote  multi-host  hostname-exits"
    exit 1
    ;;
esac

wait
