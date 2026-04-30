#!/usr/bin/env python3
"""Translate agent hook payloads into Tasklight HTTP events."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://127.0.0.1:57017/hook"


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _nested_get(mapping: dict[str, Any], *path: str) -> Any:
    current: Any = mapping
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _guess_session_id(source: str, event: str, payload: dict[str, Any], cwd: str) -> str:
    session_id = _first_nonempty(
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("conversation_id"),
        payload.get("conversationId"),
        payload.get("thread_id"),
        payload.get("threadId"),
        _nested_get(payload, "session", "id"),
        _nested_get(payload, "session", "session_id"),
        _nested_get(payload, "event", "session_id"),
    )
    if session_id is not None:
        return session_id
    return f"{source}:{cwd}:{event}:{os.getpid()}"


def _guess_cwd(payload: dict[str, Any]) -> str:
    cwd = _first_nonempty(
        payload.get("cwd"),
        payload.get("project_dir"),
        payload.get("projectDir"),
        payload.get("working_directory"),
        payload.get("workingDirectory"),
        payload.get("directory"),
        payload.get("workspace_root"),
        payload.get("workspaceRoot"),
        payload.get("root"),
        payload.get("worktree"),
        _nested_get(payload, "project", "path"),
        _nested_get(payload, "session", "cwd"),
    )
    return cwd or os.getcwd()


def _guess_tool_name(payload: dict[str, Any]) -> str | None:
    return _first_nonempty(
        payload.get("tool_name"),
        payload.get("toolName"),
        payload.get("tool"),
        _nested_get(payload, "tool", "name"),
        _nested_get(payload, "tool", "tool_name"),
    )


def _map_event(source: str, hook_event: str) -> str | None:
    mappings = {
        "claude-code": {
            "SessionStart": "start",
            "UserPromptSubmit": "thinking",
            "PreToolUse": "tool_use",
            "PostToolUse": "thinking",
            "Stop": "stop",
            "SessionEnd": "exit",
        },
        "codex": {
            "SessionStart": "start",
            "UserPromptSubmit": "thinking",
            "PreToolUse": "tool_use",
            "PostToolUse": "thinking",
            "Stop": "stop",
        },
    }
    return mappings.get(source, {}).get(hook_event)


def _post_json(url: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=1):
            pass
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Send Tasklight hook payload")
    parser.add_argument("--source", required=True, choices=["claude-code", "codex"])
    parser.add_argument("--event", required=True)
    parser.add_argument("--url", default=os.environ.get("TASKLIGHT_URL", DEFAULT_URL))
    args = parser.parse_args()

    raw_payload = _read_stdin_json()
    mapped_event = _map_event(args.source, args.event)
    if mapped_event is None:
        return 0

    cwd = _guess_cwd(raw_payload)
    payload = {
        "source": args.source,
        "session_id": _guess_session_id(args.source, args.event, raw_payload, cwd),
        "cwd": cwd,
        "event": mapped_event,
        "data": {},
    }

    if mapped_event == "tool_use":
        tool_name = _guess_tool_name(raw_payload)
        payload["data"] = {"tool_name": tool_name or "tool"}

    _post_json(args.url, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
