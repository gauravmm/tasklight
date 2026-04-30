"""HTTP hook server running in a background QThread."""

import ipaddress
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QThread, pyqtSignal

PORT = 57017
_REQUIRED_KEYS = {"source", "session_id", "cwd", "event"}

# On Windows, accept loopback and WSL2's NAT subnet in addition to loopback.
_ALLOWED_NETWORKS = [ipaddress.ip_network("127.0.0.0/8")]
if sys.platform == "win32":
    _ALLOWED_NETWORKS.append(ipaddress.ip_network("172.16.0.0/12"))


def _is_allowed(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
        return any(ip in net for net in _ALLOWED_NETWORKS)
    except ValueError:
        return False


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # silence default stderr logging

    def do_GET(self):
        if not _is_allowed(self.client_address[0]):
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
            "data": {"tool_name": first("tool_name") or "tool"} if event == "tool_use" else {},
        }

        self.server.hook_server.event_received.emit(payload)

    def do_POST(self):
        if not _is_allowed(self.client_address[0]):
            self._respond(403)
            return

        if self.path != "/hook":
            self._respond(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self._respond(204)  # respond immediately before any processing

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return

        if not _REQUIRED_KEYS.issubset(payload):
            return

        self.server.hook_server.event_received.emit(payload)

    def _respond(self, code: int) -> None:
        self.send_response(code)
        self.end_headers()


class HookServer(QThread):
    event_received = pyqtSignal(dict)

    def __init__(self, port: int = PORT, parent=None):
        super().__init__(parent)
        self._port = port
        self._server: HTTPServer | None = None

    def run(self) -> None:
        bind = "0.0.0.0" if sys.platform == "win32" else "127.0.0.1"
        self._server = HTTPServer((bind, self._port), _Handler)
        self._server.hook_server = self
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
