"""HTTP hook server running in a background QThread."""

import ipaddress
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QThread, pyqtSignal

PORT = 57017
_REQUIRED_KEYS = {"source", "session_id", "cwd", "event"}


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

        self.server.hook_server.event_received.emit(payload)

    def do_POST(self):
        if not self._allowed():
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
