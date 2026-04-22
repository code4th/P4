from __future__ import annotations
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from p4_core.dashboard.snapshot import build_snapshot, _reasoning_model
from p4_core.dashboard.templates import render_dashboard_html
from p4_core.workspace import active_session_id, append_session_event, now_iso, WorkspacePaths, read_json, read_jsonl, write_json
import uuid

_EVENT_CONDITION = threading.Condition()
_LATEST_SNAPSHOT: dict[str, Any] | None = None

def _set_latest_snapshot(snapshot: dict[str, Any]) -> None:
    global _LATEST_SNAPSHOT
    with _EVENT_CONDITION:
        _LATEST_SNAPSHOT = snapshot
        _EVENT_CONDITION.notify_all()

def _update_runtime(root: Path, **fields: Any) -> None:
    paths = WorkspacePaths(root)
    current = read_json(paths.runtime_status_path, fallback={})
    payload = {
        **current,
        **fields,
        "last_event_at": now_iso(),
    }
    write_json(paths.runtime_status_path, payload)
    _set_latest_snapshot(build_snapshot(root))

def create_dashboard_server(
    root: Path,
    *,
    host: str,
    port: int,
    chat_runner: Callable[[Path, str, str, str, str], None] | None = None,
) -> ThreadingHTTPServer:
    from p4_core.runtime import AgentRuntime
    from p4_core.ollama_client import OllamaChatClient

    if chat_runner is None:
        def _default_runner(current_root: Path, content: str, model: str, mode: str, shell_name: str) -> None:
            if mode == "terminal_agent":
                # We need to reuse the runtime or its logic
                # For simplicity in this move, we call the runner directly
                _run_terminal_agent(current_root, content, model, shell_name)
                return
            _run_ollama_chat(current_root, content, model)
        chat_runner = _default_runner

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/health":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/snapshot":
                snapshot = build_snapshot(self.server.root)
                body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last_payload = ""
                try:
                    while True:
                        current = build_snapshot(self.server.root)
                        payload = json.dumps(current, ensure_ascii=False, sort_keys=True)
                        if payload != last_payload:
                            last_payload = payload
                            self.wfile.write(f"event: snapshot\ndata: {payload}\n\n".encode("utf-8"))
                            self.wfile.flush()
                        time.sleep(1.0) # Polling fallback in SSE stream
                except Exception: return
            if self.path in {"/", "/index.html"}:
                snapshot = build_snapshot(self.server.root)
                body = render_dashboard_html(snapshot).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path == "/api/message":
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    self.send_error(400); return
                content = str(data.get("content") or "").strip()
                model = str(data.get("model") or _reasoning_model(self.server.root))
                mode = str(data.get("mode") or "native_chat")
                shell_name = str(data.get("shell") or "auto")
                if not content:
                    self.send_error(400); return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"ok": True, "model": model, "mode": mode, "shell": shell_name},
                        ensure_ascii=False,
                    ).encode("utf-8")
                )
                threading.Thread(
                    target=chat_runner,
                    args=(self.server.root, content, model, mode, shell_name),
                    daemon=True,
                ).start()
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer((host, port), _Handler)
    server.root = Path(root).expanduser().resolve()
    return server

def _append_activity_update(root: Path, session_id: str, message: str, *, status: str = "info", operation_id: str | None = None) -> None:
    payload = {"type": "activity_update", "role": "system", "status": status, "content": str(message)}
    if operation_id:
        payload["operation_id"] = operation_id
    append_session_event(root, session_id, payload)

def _append_operation_event(root: Path, session_id: str, *, operation_id: str, title: str, detail: str, status: str, started_at: str | None = None, finished_at: str | None = None, duration_ms: int | None = None, output_preview: str | None = None) -> None:
    append_session_event(root, session_id, {"type": "operation", "role": "system", "operation_id": operation_id, "title": title, "detail": detail, "status": status, "started_at": started_at, "finished_at": finished_at, "duration_ms": duration_ms, "output_preview": output_preview})

def _duration_ms(started_at: str, finished_at: str) -> int | None:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
    except Exception: return None
    return max(0, int((finish - start).total_seconds() * 1000))

from datetime import datetime

def _latest_finish_blocked_reason(root: Path, session_id: str, started_at: str) -> tuple[str, str] | None:
    started = None
    try:
        started = datetime.fromisoformat(started_at)
    except Exception:
        pass
    events = read_jsonl(WorkspacePaths(root).session_events_path(session_id), limit=200)
    latest_blocked: tuple[str, str] | None = None
    latest_finish_at: datetime | None = None
    latest_blocked_at: datetime | None = None
    for row in events:
        row_time = None
        try:
            row_time = datetime.fromisoformat(str(row.get("timestamp") or ""))
        except Exception:
            pass
        if started is not None and row_time is not None and row_time < started:
            continue
        row_type = str(row.get("type") or "")
        if row_type == "finish":
            latest_finish_at = row_time
            continue
        if row_type != "system_note":
            continue
        is_blocked = str(row.get("code") or "") == "finish_blocked" or "完了がブロックされました" in str(row.get("content") or "")
        if is_blocked:
            latest_blocked_at = row_time
            latest_blocked = (str(row.get("content") or ""), str(row.get("reason_code") or ""))
    if latest_blocked is None:
        return None
    if latest_finish_at is not None and latest_blocked_at is not None and latest_finish_at > latest_blocked_at:
        return None
    return latest_blocked

def _observer_enabled(root: Path) -> bool:
    config = read_json(WorkspacePaths(root).config_path, fallback={})
    runtime_config = config.get("runtime", {}) if isinstance(config, dict) else {}
    return bool(runtime_config.get("observer_enabled"))

def _append_native_commentary(
    root: Path,
    session_id: str,
    *,
    operation_id: str,
    model: str,
    user_message: str,
    prompt: str,
    llm_answer: str,
    system_status: str,
    system_reason: str,
) -> None:
    if not _observer_enabled(root):
        return
    config = read_json(WorkspacePaths(root).config_path, fallback={})
    runtime_config = config.get("runtime", {}) if isinstance(config, dict) else {}
    models = config.get("models", {}) if isinstance(config, dict) else {}
    observer_model = str(runtime_config.get("observer_model") or models.get("fast") or "fast")
    options = dict(runtime_config.get("observer_options") or {"temperature": 0.2, "num_predict": 260})
    base_url = str(config.get("ollama_base_url") or "http://127.0.0.1:11434")
    observer_prompt = (
        "あなたはAIエージェント研究開発のエキスパートであり、P4実験の実況解説者です。"
        "システムとLLMのやりとりを観察し、何が起きたか、システム判定が妥当か、"
        "改善すべき処理があるかを日本語で解説してください。"
        "失敗やブロックが起きた場合は、LLMがなぜその出力に至ったかを、渡されたコンテキスト、"
        "直近応答、証拠不足、指示の衝突や混入の観点から点検してください。介入や指示はせず、観測と解説だけを行います。\n"
        "次の5項目で簡潔に書いてください。\n"
        "1. 入力とコンテキスト\n"
        "2. LLMの実行と回答\n"
        "3. システム判定\n"
        "4. 失敗要因の仮説\n"
        "5. コンテキスト点検\n\n"
        f"ユーザー入力: {user_message}\n\n"
        f"LLMに渡したコンテキスト/プロンプト:\n{prompt[:3000]}\n\n"
        f"LLM実行: model={model}, endpoint=POST /api/generate\n\n"
        f"LLM回答:\n{llm_answer[:6000]}\n\n"
        f"システム判定: {system_status}\n"
        f"システム理由: {system_reason}\n"
    )
    try:
        response = OllamaChatClient(base_url=base_url).chat(
            model=observer_model,
            messages=[{"role": "user", "content": observer_prompt}],
            options=options,
            timeout_seconds=int(runtime_config.get("chat_timeout_seconds") or 180),
        )
        content = str(response.get("content_text") or response.get("content") or "").strip()
        if not content:
            content = "実況解説者は空の応答を返しました。"
        append_session_event(
            root,
            session_id,
            {
                "type": "observer_note",
                "role": "observer",
                "content": content,
                "model": response.get("model", observer_model),
                "code": "live_commentator",
                "reason_code": "native_chat_commentary",
                "details": {
                    "observer_prompt": observer_prompt,
                    "system_status": system_status,
                    "system_reason": system_reason,
                    "raw_response": str(response.get("content") or ""),
                    "content_text": str(response.get("content_text") or ""),
                    "thinking_text": str(response.get("thinking_text") or ""),
                },
                "operation_id": operation_id,
                "step_index": 2,
            },
        )
    except Exception as exc:
        append_session_event(
            root,
            session_id,
            {
                "type": "observer_note",
                "role": "observer",
                "content": f"実況解説者の生成に失敗しました: {exc}",
                "code": "live_commentator",
                "reason_code": "observer_error",
                "operation_id": operation_id,
                "step_index": 2,
            },
        )

def _run_ollama_chat(root: Path, message: str, model: str) -> None:
    from p4_core.ollama_client import OllamaChatClient
    config = read_json(WorkspacePaths(root).config_path, fallback={})
    base_url = str(config.get("ollama_base_url") or "http://127.0.0.1:11434")
    client = OllamaChatClient(base_url=base_url)
    session_id = active_session_id(root)
    operation_id = uuid.uuid4().hex
    started_at = now_iso()
    request_detail = f"POST /api/generate\nmodel: {model}\nprompt: {message}"
    append_session_event(root, session_id, {"type": "user_message", "role": "user", "content": message, "operation_id": operation_id, "step_index": 0})
    _append_activity_update(root, session_id, f"{model} の native generate を開始します。", operation_id=operation_id)
    _append_operation_event(root, session_id, operation_id=operation_id, title="Native generate stream", detail=request_detail, status="running", started_at=started_at, output_preview="")
    _update_runtime(root, status="running", current_model=model, current_model_reason="ollama native generate stream", current_user_message=message, current_stream_text="", current_operation_id=operation_id, current_started_at=started_at, current_finished_at=None)
    stream_parts: list[str] = []
    try:
        for chunk in client.iter_generate_stream(model=model, prompt=message, timeout_seconds=600):
            content = str(chunk.get("response") or "")
            if content: stream_parts.append(content)
            _update_runtime(root, status="running", current_stream_text=("".join(stream_parts))[-16000:])
    except Exception as exc:
        finished_at = now_iso()
        _append_activity_update(root, session_id, f"native generate 失敗: {exc}", status="error", operation_id=operation_id)
        _append_operation_event(root, session_id, operation_id=operation_id, title="Native generate stream", detail=request_detail, status="failed", started_at=started_at, finished_at=finished_at, duration_ms=_duration_ms(started_at, finished_at), output_preview=("".join(stream_parts))[-4000:])
        _append_native_commentary(
            root,
            session_id,
            operation_id=operation_id,
            model=model,
            user_message=message,
            prompt=message,
            llm_answer="".join(stream_parts).strip(),
            system_status="failed",
            system_reason=f"native generate 失敗: {exc}",
        )
        _update_runtime(
            root,
            status="idle",
            last_error=str(exc),
            current_user_message=None,
            current_stream_text="",
            current_operation_id=None,
            current_started_at=None,
            current_finished_at=finished_at,
        )
        return
    finished_at = now_iso()
    final_answer = "".join(stream_parts).strip()
    _append_operation_event(root, session_id, operation_id=operation_id, title="Native generate stream", detail=request_detail, status="success", started_at=started_at, finished_at=finished_at, duration_ms=_duration_ms(started_at, finished_at), output_preview=("".join(stream_parts))[-4000:])
    append_session_event(root, session_id, {"type": "assistant_message", "role": "assistant", "content": final_answer, "model": model, "operation_id": operation_id, "step_index": 1})
    _append_activity_update(root, session_id, "native generate は正常終了しました。システムは回答を却下していません。", status="success", operation_id=operation_id)
    _append_native_commentary(
        root,
        session_id,
        operation_id=operation_id,
        model=model,
        user_message=message,
        prompt=message,
        llm_answer=final_answer,
        system_status="success",
        system_reason="native generate が完了し、システム側の却下・ブロックは発生していません。",
    )
    _update_runtime(
        root,
        status="idle",
        current_user_message=None,
        current_stream_text="",
        current_operation_id=None,
        current_started_at=None,
        current_finished_at=finished_at,
    )

def _run_terminal_agent(root: Path, message: str, model: str, shell_name: str) -> None:
    from p4_core.runtime import AgentRuntime
    config = read_json(WorkspacePaths(root).config_path, fallback={})
    session_id = active_session_id(root)
    operation_id = uuid.uuid4().hex
    started_at = now_iso()
    request_detail = f"mode: terminal agent\nmodel: {model}\nshell: {shell_name}\nmessage: {message}"
    _append_activity_update(root, session_id, f"terminal agent を開始します ({shell_name})。")
    _append_operation_event(root, session_id, operation_id=operation_id, title="Terminal agent", detail=request_detail, status="running", started_at=started_at, output_preview="")
    _update_runtime(root, status="running", current_operation_id=operation_id, current_started_at=started_at)
    runtime = AgentRuntime(root)
    try:
        result = runtime.run_terminal_agent(message, model=model, shell_name=shell_name)
        finished_at = now_iso()
        last_result = (result.get("run") or {}).get("last_result") or {}
        ok = bool(last_result.get("ok"))
        blocked = _latest_finish_blocked_reason(root, session_id, started_at)
        if blocked is not None:
            blocked_reason, reason_code = blocked
            status = "blocked"
            preview = f"{blocked_reason}\nreason_code: {reason_code}".strip()
            _append_activity_update(root, session_id, f"terminal agent はブロックされました: {reason_code}", status="blocked", operation_id=operation_id)
        else:
            status = "success" if ok else "failed"
            preview = str(last_result.get("final_answer") or last_result.get("error") or "")[-4000:]
            _append_activity_update(root, session_id, f"terminal agent 終了: {status}", status=("success" if ok else "error"), operation_id=operation_id)
        _append_operation_event(root, session_id, operation_id=operation_id, title="Terminal agent", detail=request_detail, status=status, started_at=started_at, finished_at=finished_at, duration_ms=_duration_ms(started_at, finished_at), output_preview=preview[-4000:])
    except Exception as exc:
        finished_at = now_iso()
        _append_activity_update(root, session_id, f"terminal agent 失敗: {exc}", status="error")
        _append_operation_event(root, session_id, operation_id=operation_id, title="Terminal agent", detail=request_detail, status="failed", started_at=started_at, finished_at=finished_at, duration_ms=_duration_ms(started_at, finished_at), output_preview=str(exc))
    _update_runtime(
        root,
        status="idle",
        current_user_message=None,
        current_stream_text="",
        current_operation_id=None,
        current_started_at=None,
        current_finished_at=finished_at,
    )

def serve_dashboard(root: Path, *, host: str = "127.0.0.1", port: int = 8899) -> None:
    server = create_dashboard_server(root, host=host, port=port)
    try: server.serve_forever(poll_interval=0.2)
    finally: server.server_close()
