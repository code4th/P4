from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from p4_core.ollama_client import OllamaChatClient


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class OllamaClientTests(unittest.TestCase):
    def test_chat_lifts_format_and_think_to_top_level_payload(self) -> None:
        captured: dict = {}

        def fake_urlopen(req, timeout):
            del timeout
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _Response({"model": "fake", "message": {"content": "{}"}})

        schema = {"type": "object", "properties": {}, "required": []}
        with patch("p4_core.ollama_client.request.urlopen", fake_urlopen):
            OllamaChatClient(base_url="http://ollama.test").chat(
                model="fake",
                messages=[{"role": "user", "content": "x"}],
                options={"format": schema, "think": False, "temperature": 0.1},
            )

        self.assertEqual(captured["payload"]["format"], schema)
        self.assertFalse(captured["payload"]["think"])
        self.assertEqual(captured["payload"]["options"], {"temperature": 0.1})


if __name__ == "__main__":
    unittest.main()
