from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


_EVENT_CONDITION = threading.Condition()
_EVENT_SEQUENCE = 0
_LATEST_SNAPSHOT: dict[str, Any] | None = None


def _set_latest_snapshot(snapshot: dict[str, Any]) -> None:
    global _EVENT_SEQUENCE, _LATEST_SNAPSHOT
    with _EVENT_CONDITION:
        _LATEST_SNAPSHOT = snapshot
        _EVENT_SEQUENCE += 1
        _EVENT_CONDITION.notify_all()


def _health_body() -> bytes:
    return b"ok"


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _is_loopback_client(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def create_dashboard_server(
    root: Path,
    *,
    host: str,
    port: int,
    snapshot_builder: Callable[[Path], dict[str, Any]],
    html_renderer: Callable[[dict[str, Any], str], str],
    dashboard_script: str,
    goal_update_handler: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
    control_handler: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
) -> ThreadingHTTPServer:
    class _DashboardHandler(BaseHTTPRequestHandler):
        root: Path

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/health"):
                body = _health_body()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path in {"/", "/index.html"}:
                body = html_renderer(snapshot_builder(self.root), dashboard_script=dashboard_script).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.startswith("/api/snapshot"):
                body = json.dumps(snapshot_builder(self.root), ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.startswith("/api/events"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last_seen = -1
                try:
                    while True:
                        with _EVENT_CONDITION:
                            _EVENT_CONDITION.wait_for(lambda: _EVENT_SEQUENCE > last_seen, timeout=20)
                            if _EVENT_SEQUENCE <= last_seen:
                                self.wfile.write(b"event: ping\ndata: keep-alive\n\n")
                                self.wfile.flush()
                                continue
                            last_seen = _EVENT_SEQUENCE
                            current = _LATEST_SNAPSHOT or snapshot_builder(self.root)
                        body = json.dumps(current, ensure_ascii=False).encode("utf-8")
                        self.wfile.write(b"event: snapshot\ndata: ")
                        self.wfile.write(body)
                        self.wfile.write(b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            self.send_error(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/api/notify", "/api/goal", "/api/control"}:
                self.send_error(404, "not found")
                return
            if not _is_loopback_client(self.client_address[0]):
                self.send_error(403, "loopback only")
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self.send_error(400, "invalid json")
                    return
                if not isinstance(payload, dict):
                    self.send_error(400, "invalid payload")
                    return
            else:
                payload = {}

            if self.path == "/api/goal":
                if goal_update_handler is None:
                    _json_response(self, 503, {"ok": False, "error": "goal update unavailable"})
                    return
                try:
                    result = goal_update_handler(self.root, payload)
                except ValueError as exc:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    _json_response(self, 500, {"ok": False, "error": f"goal update failed: {exc}"})
                    return
                _set_latest_snapshot(snapshot_builder(self.root))
                _json_response(self, 200, result)
                return
            if self.path == "/api/control":
                if control_handler is None:
                    _json_response(self, 503, {"ok": False, "error": "control unavailable"})
                    return
                try:
                    result = control_handler(self.root, payload)
                except ValueError as exc:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    _json_response(self, 500, {"ok": False, "error": f"control failed: {exc}"})
                    return
                _set_latest_snapshot(snapshot_builder(self.root))
                _json_response(self, 200, result)
                return
            _set_latest_snapshot(snapshot_builder(self.root))
            body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            del format
            del args
            return

    handler = type("P2DashboardHandler", (_DashboardHandler,), {"root": root})
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), handler)
    _set_latest_snapshot(snapshot_builder(root))
    return server
