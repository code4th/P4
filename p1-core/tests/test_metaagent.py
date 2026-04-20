from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from p1_core.core.metaagent import OpenAICompatibleClient, SelfRepairMetaAgent


class FakeClient:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
        self.model = model

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return """```python
def meaning() -> int:
    return 42
```"""


class SelfRepairMetaAgentTests(unittest.TestCase):
    def test_metaagent_applies_and_backups_python_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            target.write_text("def meaning():\n    return 0\n", encoding="utf-8")

            with patch("p1_core.core.metaagent.OllamaClient", FakeClient):
                agent = SelfRepairMetaAgent(
                    root=root,
                    model="qwen3-coder",
                    test_command=["python3", "-m", "py_compile", str(target)],
                )
                result = agent.run(target, purpose="make the file return 42", constraints="keep it tiny")

            self.assertTrue(result.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "def meaning() -> int:\n    return 42\n")
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(Path(result.backup_path).exists())
            history = agent.history_path.read_text(encoding="utf-8")
            self.assertIn("applied proposed file revision", history)

    def test_metaagent_uses_openclaw_backend(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": """```python
def meaning() -> int:
    return 7
```"""
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.py"
            target.write_text("def meaning():\n    return 0\n", encoding="utf-8")
            config = root / "openclaw.json"
            config.write_text(
                json.dumps(
                    {
                        "gateway": {
                            "port": 18789,
                            "auth": {"token": "secret-token"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            captured = {}

            def fake_urlopen(req, timeout=None):
                captured["url"] = req.full_url
                captured["auth"] = req.headers.get("Authorization")
                captured["body"] = json.loads(req.data.decode("utf-8"))
                return FakeResponse()

            with patch("p1_core.core.metaagent.request.urlopen", fake_urlopen):
                agent = SelfRepairMetaAgent(
                    root=root,
                    model="openclaw/main",
                    backend="openclaw",
                    openclaw_config_path=config,
                    test_command=["python3", "-m", "py_compile", str(target)],
                )
                result = agent.run(target, purpose="make the file return 7", constraints="keep it tiny")

            self.assertTrue(result.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "def meaning() -> int:\n    return 7\n")
            self.assertEqual(captured["url"], "http://127.0.0.1:18789/v1/chat/completions")
            self.assertEqual(captured["auth"], "Bearer secret-token")
            self.assertEqual(captured["body"]["model"], "openclaw/main")
            history = agent.history_path.read_text(encoding="utf-8")
            self.assertIn('"backend": "openclaw"', history)
