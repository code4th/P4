from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ollama_client import OllamaClient, OllamaError
from .service import WorkerService


class WorkerHandler(BaseHTTPRequestHandler):
    service: WorkerService

    def do_POST(self) -> None:  # noqa: N802
        routes = {
            "/summarize": self.service.summarize,
            "/classify": self.service.classify,
            "/draft_lessons": self.service.draft_lessons,
        }
        handler = routes.get(self.path)
        if handler is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            response = handler(payload)
            self._write_json(HTTPStatus.OK, response)
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid json"})
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except OllamaError as exc:
            self._write_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"ok": True, "status": "healthy"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown endpoint"})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _write_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Ollama worker for P1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--log-dir",
        default=str(Path("/Users/satojunichi/Documents/openclaw/p1-core/runtime/logs/worker")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = OllamaClient(model=args.model, base_url=args.ollama_url)
    service = WorkerService(llm_client=client, log_dir=Path(args.log_dir))
    WorkerHandler.service = service
    server = ThreadingHTTPServer((args.host, args.port), WorkerHandler)
    print(json.dumps({"ok": True, "host": args.host, "port": args.port, "model": args.model}))
    server.serve_forever()


if __name__ == "__main__":
    main()
