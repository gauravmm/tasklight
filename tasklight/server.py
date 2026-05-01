"""HTTP hook server running in a background QThread."""

import ipaddress
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QThread, pyqtSignal

PORT = 57017
_REQUIRED_KEYS = {"source", "session_id", "cwd", "event"}

_log = logging.getLogger(__name__)

# Mapping from Claude Code hook_event_name to internal event name.
_CLAUDE_EVENT_MAP: dict[str, str] = {
    "SessionStart": "start",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "tool_use",
    "PostToolUse": "thinking",
    "PermissionRequest": "approval_required",
    "Stop": "stop",
    "SessionEnd": "exit",
}


def _parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """Minimal multipart/form-data parser; returns field_name -> bytes.

    Only handles the subset needed for /hook/claude-code (text fields and
    file uploads without nested multipart). Does not handle quoted boundary
    strings or charset conversion.
    """
    # Extract boundary from Content-Type header.
    m = re.search(r'boundary=([^\s;]+)', content_type)
    if not m:
        return {}
    boundary = m.group(1).strip('"\'').encode()

    delimiter = b"--" + boundary
    end_marker = b"--" + boundary + b"--"

    fields: dict[str, bytes] = {}
    # Split on delimiter lines.
    parts = body.split(delimiter)
    for part in parts:
        # Skip preamble, epilogue, and the final "--" marker.
        stripped = part.strip(b"\r\n")
        if stripped in (b"", b"--"):
            continue
        # The closing boundary tail "--" appears on the last part; strip
        # it as a literal suffix (not a byte set) if present.
        if stripped.endswith(b"\r\n--"):
            stripped = stripped[:-4]
        elif stripped.endswith(b"\n--"):
            stripped = stripped[:-3]
        elif stripped.endswith(b"--"):
            stripped = stripped[:-2]

        # Split headers from body (double CRLF or double LF).
        if b"\r\n\r\n" in stripped:
            headers_raw, _, field_body = stripped.partition(b"\r\n\r\n")
        elif b"\n\n" in stripped:
            headers_raw, _, field_body = stripped.partition(b"\n\n")
        else:
            continue

        # Strip the trailing CRLF that precedes the next delimiter.
        if field_body.endswith(b"\r\n"):
            field_body = field_body[:-2]
        elif field_body.endswith(b"\n"):
            field_body = field_body[:-1]

        # Extract name from Content-Disposition header.
        name_match = re.search(
            rb'Content-Disposition:[^\r\n]*name="([^"]+)"',
            headers_raw,
            re.IGNORECASE,
        )
        if not name_match:
            continue
        name = name_match.group(1).decode(errors="replace")
        fields[name] = field_body

    return fields


def _parse_context_tokens(value: object) -> int | None:
    """Parse context_tokens field tolerantly; return None on failure."""
    if value is None:
        return None
    try:
        ct = int(value)
        return ct if ct >= 0 else None
    except (ValueError, TypeError):
        _log.warning("context_tokens parse error (dropped): %r", value)
        return None


def _extract_usage_from_transcript_tail(tail_bytes: bytes) -> int | None:
    """Scan transcript JSONL tail in reverse for the latest message.usage.

    Returns context_tokens = input_tokens + cache_creation_input_tokens +
    cache_read_input_tokens, or None if not found.
    """
    if not tail_bytes:
        return None

    lines = tail_bytes.split(b"\n")
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Leading line may be truncated mid-JSON; that's expected.
            continue
        usage = obj.get("message", {})
        if isinstance(usage, dict):
            usage = usage.get("usage")
        if isinstance(usage, dict):
            input_t = usage.get("input_tokens") or 0
            cache_creation = usage.get("cache_creation_input_tokens") or 0
            cache_read = usage.get("cache_read_input_tokens") or 0
            total = input_t + cache_creation + cache_read
            # Guard against all-zero "usage" blocks: those would pin the
            # sparkline to zero and don't represent a real measurement.
            if total > 0:
                return total
    return None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # silence default stderr logging

    def _allowed(self) -> bool:
        return self.server.hook_server.is_allowed(self.client_address[0])

    def do_GET(self):
        if not self._allowed():
            self._respond(403)
            return

        parsed = urlparse(self.path)
        if parsed.path != "/hook":
            self._respond(404)
            return

        self._respond(204)

        params = parse_qs(parsed.query, keep_blank_values=False)

        def first(key):
            vals = params.get(key)
            return vals[0] if vals else None

        source = first("source")
        session_id = first("session_id")
        cwd = first("cwd")
        event = first("event")

        if not all([source, session_id, cwd, event]):
            return

        payload = {
            "source": source,
            "session_id": session_id,
            "cwd": cwd,
            "event": event,
            "hostname": first("hostname") or "",
            "data": {"tool_name": first("tool_name") or "tool"} if event == "tool_use" else {},
        }

        ct = _parse_context_tokens(first("context_tokens"))
        if ct is not None:
            payload["context_tokens"] = ct

        self.server.hook_server.event_received.emit(payload)

    def do_POST(self):
        if not self._allowed():
            self._respond(403)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/hook":
            self._handle_post_hook()
        elif path == "/hook/claude-code":
            self._handle_post_claude_code()
        else:
            self._respond(404)

    def _handle_post_hook(self) -> None:
        """Handle POST /hook — generic normalised event."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self._respond(204)  # respond immediately before any processing

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return

        if not _REQUIRED_KEYS.issubset(payload):
            return

        # Parse optional context_tokens tolerantly.
        raw_ct = payload.get("context_tokens")
        if raw_ct is not None:
            ct = _parse_context_tokens(raw_ct)
            if ct is not None:
                payload["context_tokens"] = ct
            else:
                payload.pop("context_tokens", None)

        self.server.hook_server.event_received.emit(payload)

    def _handle_post_claude_code(self) -> None:
        """Handle POST /hook/claude-code — multipart, Claude Code native hook JSON."""
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Always respond before processing.
        self._respond(204)

        # Parse multipart/form-data using a minimal inline parser.
        hook_json_bytes: bytes | None = None
        transcript_tail_bytes: bytes = b""

        try:
            fields = _parse_multipart(content_type, body)
            hook_field = fields.get("hook")
            if hook_field is not None:
                hook_json_bytes = hook_field

            tail_field = fields.get("transcript_tail")
            if tail_field is not None:
                transcript_tail_bytes = tail_field
        except Exception as exc:
            _log.warning("claude-code hook: multipart parse error: %s", exc)
            return

        if hook_json_bytes is None:
            _log.warning("claude-code hook: missing 'hook' field")
            return

        # Parse hook JSON.
        try:
            hook = json.loads(hook_json_bytes)
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("claude-code hook: hook JSON parse error: %s", exc)
            return

        session_id = hook.get("session_id")
        cwd = hook.get("cwd")
        hook_event_name = hook.get("hook_event_name")

        if not all([session_id, cwd, hook_event_name]):
            _log.warning(
                "claude-code hook: missing required field(s): %s",
                [k for k in ("session_id", "cwd", "hook_event_name") if not hook.get(k)],
            )
            return

        # Map hook_event_name -> internal event.
        event = _CLAUDE_EVENT_MAP.get(hook_event_name)
        if event is None:
            # Unknown event name — silently ignore.
            return

        # Hostname from header or peer address.
        hostname = self.headers.get("X-Tasklight-Hostname") or self.client_address[0]

        payload: dict = {
            "source": "claude-code",
            "session_id": session_id,
            "cwd": cwd,
            "event": event,
            "hostname": hostname,
            "data": {},
        }

        if event == "tool_use":
            tool_name = hook.get("tool_name") or "tool"
            payload["data"] = {"tool_name": tool_name}

        # Extract context_tokens from transcript tail.
        ct = _extract_usage_from_transcript_tail(transcript_tail_bytes)
        if ct is not None:
            payload["context_tokens"] = ct

        self.server.hook_server.event_received.emit(payload)

    def _respond(self, code: int) -> None:
        self.send_response(code)
        self.end_headers()


class HookServer(QThread):
    event_received = pyqtSignal(dict)

    def __init__(self, port: int = PORT, allowed_subnets: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._port = port
        self._server: HTTPServer | None = None
        self._allowed_networks: list = []
        self.update_subnets(allowed_subnets or ["127.0.0.0/8"])

    def update_subnets(self, subnets: list[str]) -> None:
        compiled = []
        for s in subnets:
            try:
                compiled.append(ipaddress.ip_network(s, strict=False))
            except ValueError:
                pass
        self._allowed_networks = compiled

    def is_allowed(self, addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
            return any(ip in net for net in self._allowed_networks)
        except ValueError:
            return False

    def run(self) -> None:
        self._server = HTTPServer(("0.0.0.0", self._port), _Handler)
        self._server.hook_server = self
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
