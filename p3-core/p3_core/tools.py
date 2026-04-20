from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\bgit\s+reset\s+--hard\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
]

MULTI_COMMAND_PATTERN = re.compile(r"(&&|\|\||;|\n)")


class ToolExecutor:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def specs(self) -> list[dict[str, Any]]:
        return [
            {"name": "list_files", "args": {"path": "relative path, optional"}, "description": "List files under the workspace."},
            {"name": "read_file", "args": {"path": "relative file path"}, "description": "Read a UTF-8 text file."},
            {"name": "search_code", "args": {"query": "text or regex", "path": "relative path, optional"}, "description": "Search files with ripgrep."},
            {"name": "write_file", "args": {"path": "relative file path", "content": "new file content"}, "description": "Write a small UTF-8 text file. For large files, create a short starter file and append chunks."},
            {"name": "append_file", "args": {"path": "relative file path", "content": "content chunk"}, "description": "Append a small UTF-8 content chunk to a file."},
            {"name": "replace_text", "args": {"path": "relative file path", "old_text": "exact text to replace", "new_text": "replacement text"}, "description": "Replace one exact text block in an existing file."},
            {
                "name": "run_command",
                "args": {"command": "shell command", "timeout_seconds": "optional int", "shell": "optional: auto|zsh|bash|sh|powershell"},
                "description": "Run a shell command inside the workspace.",
            },
            {"name": "finish", "args": {"final_answer": "final answer for the user"}, "description": "Mark the task as complete."},
        ]

    def describe_for_prompt(self) -> str:
        lines = []
        for spec in self.specs():
            lines.append(f"- {spec['name']}: {spec['description']} args={spec['args']}")
        return "\n".join(lines)

    def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if tool_name == "list_files":
            return self._list_files(path=str(tool_args.get("path") or "."))
        if tool_name == "read_file":
            return self._read_file(path=str(tool_args.get("path") or ""))
        if tool_name == "search_code":
            return self._search_code(
                query=str(tool_args.get("query") or ""),
                path=str(tool_args.get("path") or "."),
            )
        if tool_name == "write_file":
            return self._write_file(
                path=str(tool_args.get("path") or ""),
                content=str(tool_args.get("content") or ""),
            )
        if tool_name == "append_file":
            return self._append_file(
                path=str(tool_args.get("path") or ""),
                content=str(tool_args.get("content") or ""),
            )
        if tool_name == "replace_text":
            return self._replace_text(
                path=str(tool_args.get("path") or ""),
                old_text=str(tool_args.get("old_text") or ""),
                new_text=str(tool_args.get("new_text") or ""),
            )
        if tool_name == "run_command":
            return self._run_command(
                command=str(tool_args.get("command") or ""),
                timeout_seconds=int(tool_args.get("timeout_seconds") or 60),
                shell_name=str(tool_args.get("shell") or "auto"),
                on_update=on_update,
            )
        raise ValueError(f"unsupported tool: {tool_name}")

    def _resolve_path(self, path: str) -> Path:
        clean = str(path or "").strip()
        if not clean:
            raise ValueError("path is required")
        candidate = (self.root / clean).resolve() if not Path(clean).is_absolute() else Path(clean).resolve()
        if os.path.commonpath([str(self.root), str(candidate)]) != str(self.root):
            raise ValueError("path escapes workspace root")
        return candidate

    def _list_files(self, *, path: str) -> dict[str, Any]:
        target = self._resolve_path(path if path != "." else str(self.root))
        if target.is_file():
            rel = target.relative_to(self.root)
            return {"ok": True, "tool": "list_files", "items": [str(rel)]}
        try:
            proc = subprocess.run(
                ["rg", "--files", str(target)],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                items = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                items = [str(Path(item).resolve().relative_to(self.root)) for item in items if Path(item).exists()]
                return {"ok": True, "tool": "list_files", "items": items[:200]}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        items = []
        for file_path in sorted(target.rglob("*")):
            if file_path.is_file():
                items.append(str(file_path.relative_to(self.root)))
                if len(items) >= 200:
                    break
        return {"ok": True, "tool": "list_files", "items": items}

    def _read_file(self, *, path: str) -> dict[str, Any]:
        target = self._resolve_path(path)
        if not target.exists():
            raise ValueError(f"file does not exist: {path}")
        return {
            "ok": True,
            "tool": "read_file",
            "path": str(target.relative_to(self.root)),
            "content": target.read_text(encoding="utf-8"),
        }

    def _search_code(self, *, query: str, path: str) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query is required")
        target = self._resolve_path(path if path != "." else str(self.root))
        try:
            proc = subprocess.run(
                ["rg", "-n", "--hidden", "--glob", "!.git", query, str(target)],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            lines = (proc.stdout or "").splitlines()[:200]
            return {"ok": True, "tool": "search_code", "matches": lines}
        except FileNotFoundError as exc:
            raise RuntimeError("rg is required for search_code") from exc

    def _write_file(self, *, path: str, content: str) -> dict[str, Any]:
        target = self._resolve_path(path)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > 2000:
            return {
                "ok": False,
                "tool": "write_file",
                "path": str(target.relative_to(self.root)),
                "error": "write_file content is too large for one JSON tool call; create a small file first and use append_file chunks of 2000 bytes or less",
                "bytes_requested": content_bytes,
                "max_bytes": 2000,
                "suggested_tool": "append_file",
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "tool": "write_file",
            "path": str(target.relative_to(self.root)),
            "bytes_written": content_bytes,
        }

    def _append_file(self, *, path: str, content: str) -> dict[str, Any]:
        target = self._resolve_path(path)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > 2000:
            return {
                "ok": False,
                "tool": "append_file",
                "path": str(target.relative_to(self.root)),
                "error": "append_file content chunk is too large; split it into chunks of 2000 bytes or less",
                "bytes_requested": content_bytes,
                "max_bytes": 2000,
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return {
            "ok": True,
            "tool": "append_file",
            "path": str(target.relative_to(self.root)),
            "bytes_appended": content_bytes,
            "total_bytes": target.stat().st_size,
        }

    def _replace_text(self, *, path: str, old_text: str, new_text: str) -> dict[str, Any]:
        if not old_text:
            raise ValueError("old_text is required")
        target = self._resolve_path(path)
        if not target.exists():
            raise ValueError(f"file does not exist: {path}")
        content = target.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count != 1:
            return {
                "ok": False,
                "tool": "replace_text",
                "path": str(target.relative_to(self.root)),
                "error": f"old_text must match exactly once; matched {count} times",
                "matches": count,
            }
        updated = content.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "tool": "replace_text",
            "path": str(target.relative_to(self.root)),
            "bytes_written": len(updated.encode("utf-8")),
        }

    def _run_command(
        self,
        *,
        command: str,
        timeout_seconds: int,
        shell_name: str,
        on_update: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        clean = command.strip()
        if not clean:
            raise ValueError("command is required")
        if MULTI_COMMAND_PATTERN.search(clean):
            raise ValueError("run_command accepts exactly one command per step; chaining multiple commands is not allowed")
        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, clean):
                raise ValueError(f"command denied by safety policy: {clean}")
        argv, resolved_shell = self._command_argv(clean, shell_name)
        started_at = time.time()
        started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at))
        proc = subprocess.Popen(
            argv,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _pump(handle: Any, sink: list[str], stream_name: str) -> None:
            if handle is None:
                return
            for chunk in iter(handle.readline, ""):
                sink.append(chunk)
                if on_update is not None:
                    on_update(
                        {
                            "ok": None,
                            "tool": "run_command",
                            "command": clean,
                            "shell": resolved_shell,
                            "cwd": str(self.root),
                            "stdout": "".join(stdout_chunks)[-4000:],
                            "stderr": "".join(stderr_chunks)[-4000:],
                            "active_stream": stream_name,
                        }
                    )
            handle.close()

        stdout_thread = threading.Thread(target=_pump, args=(proc.stdout, stdout_chunks, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=_pump, args=(proc.stderr, stderr_chunks, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = proc.wait(timeout=timeout_seconds)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            finished_at = time.time()
            finished_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at))
            duration_ms = int((finished_at - started_at) * 1000)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            finished_at = time.time()
            finished_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at))
            duration_ms = int((finished_at - started_at) * 1000)
            return {
                "ok": False,
                "tool": "run_command",
                "command": clean,
                "shell": resolved_shell,
                "cwd": str(self.root),
                "started_at": started_iso,
                "finished_at": finished_iso,
                "duration_ms": duration_ms,
                "returncode": None,
                "stdout": "".join(stdout_chunks)[-4000:],
                "stderr": ("".join(stderr_chunks) + f"\nTimed out after {timeout_seconds}s")[-4000:],
            }
        return {
            "ok": returncode == 0,
            "tool": "run_command",
            "command": clean,
            "shell": resolved_shell,
            "cwd": str(self.root),
            "started_at": started_iso,
            "finished_at": finished_iso,
            "duration_ms": duration_ms,
            "returncode": returncode,
            "stdout": "".join(stdout_chunks)[-4000:],
            "stderr": "".join(stderr_chunks)[-4000:],
        }

    def _command_argv(self, command: str, shell_name: str) -> tuple[list[str], str]:
        requested = str(shell_name or "auto").strip().lower()
        if requested in {"", "auto"}:
            env_shell = Path(os.environ.get("SHELL") or "").name.lower()
            requested = env_shell if env_shell in {"zsh", "bash", "sh"} else "bash"
        if requested in {"powershell", "pwsh"}:
            if shutil.which("pwsh") is None:
                raise ValueError("PowerShell requested but 'pwsh' is not installed in this environment")
            return ["pwsh", "-NoLogo", "-NoProfile", "-Command", command], "powershell"
        if requested not in {"zsh", "bash", "sh"}:
            raise ValueError(f"unsupported shell: {shell_name}")
        return [requested, "-lc", command], requested
