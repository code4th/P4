from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}


def build_repo_map(root: Path, *, max_files: int = 120, max_symbols: int = 240) -> dict[str, Any]:
    """Build a small repository index for agent-facing context.

    This is intentionally a context map, not a correctness oracle. It avoids
    domain knowledge and gives the model enough structure to choose targeted
    reads and edits.
    """

    workspace = Path(root).expanduser().resolve()
    files: list[str] = []
    python_files: list[str] = []
    test_files: list[str] = []
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []

    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        parts = set(relative.parts)
        if parts & IGNORED_DIRS:
            continue
        rel_text = str(relative).replace("\\", "/")
        files.append(rel_text)
        if path.suffix == ".py":
            python_files.append(rel_text)
            name = path.name
            if rel_text.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py"):
                test_files.append(rel_text)
            if len(symbols) < max_symbols:
                file_symbols, file_imports = _python_file_map(path, rel_text)
                remaining = max(0, max_symbols - len(symbols))
                symbols.extend(file_symbols[:remaining])
                imports.extend(file_imports[: max(0, max_symbols - len(imports))])
        if len(files) >= max_files:
            break

    return {
        "root": str(workspace),
        "files": files,
        "python_files": python_files,
        "test_files": test_files,
        "symbols": symbols,
        "imports": imports[:max_symbols],
        "truncated": len(files) >= max_files or len(symbols) >= max_symbols,
    }


def format_repo_map_for_prompt(repo_map: dict[str, Any], *, max_chars: int = 1600) -> str:
    files = [str(item) for item in repo_map.get("files") or []]
    symbols = [item for item in repo_map.get("symbols") or [] if isinstance(item, dict)]
    imports = [item for item in repo_map.get("imports") or [] if isinstance(item, dict)]
    if not files and not symbols:
        return ""

    lines = ["repo_map:"]
    if files:
        lines.append("- files: " + ", ".join(files[:40]))
    test_files = [str(item) for item in repo_map.get("test_files") or []]
    if test_files:
        lines.append("- test_files: " + ", ".join(test_files[:20]))
    if symbols:
        lines.append("- symbols:")
        for item in symbols[:40]:
            name = str(item.get("name") or "")
            signature = str(item.get("signature") or item.get("name") or "")
            display = signature if not name or name in signature else f"{name} {signature}"
            lines.append(f"  - {item.get('path')}:{item.get('line')} {item.get('kind')} {display}")
    if imports:
        import_parts = []
        for item in imports[:30]:
            names = ", ".join(str(name) for name in item.get("names") or [])
            import_parts.append(f"{item.get('path')}:{names}")
        lines.append("- imports: " + "; ".join(import_parts))
    if repo_map.get("truncated"):
        lines.append("- truncated: true")

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 20)].rstrip() + "\n- truncated_prompt: true"


def _python_file_map(path: Path, rel_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return [], []

    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append(_symbol(rel_text, "function", node.name, node.lineno, _signature(node)))
            continue
        if isinstance(node, ast.ClassDef):
            symbols.append(_symbol(rel_text, "class", node.name, node.lineno, f"class {node.name}"))
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    symbols.append(
                        _symbol(rel_text, "method", f"{node.name}.{child.name}", child.lineno, _signature(child))
                    )
            continue
        if isinstance(node, ast.Import):
            imports.append({"path": rel_text, "line": node.lineno, "names": [alias.name for alias in node.names]})
            continue
        if isinstance(node, ast.ImportFrom):
            module = "." * int(node.level or 0) + str(node.module or "")
            names = [f"{module}.{alias.name}".strip(".") for alias in node.names]
            imports.append({"path": rel_text, "line": node.lineno, "names": names})
    return symbols, imports


def _symbol(path: str, kind: str, name: str, line: int, signature: str) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "name": name,
        "line": line,
        "signature": signature,
    }


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args = []
    positional = [*node.args.posonlyargs, *node.args.args]
    for arg in positional:
        args.append(arg.arg)
    if node.args.vararg is not None:
        args.append("*" + node.args.vararg.arg)
    for arg in node.args.kwonlyargs:
        args.append(arg.arg)
    if node.args.kwarg is not None:
        args.append("**" + node.args.kwarg.arg)
    return f"{prefix} {node.name}({', '.join(args)})"
