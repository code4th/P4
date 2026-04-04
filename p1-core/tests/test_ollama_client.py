from __future__ import annotations

import unittest

from p1_core.worker.ollama_client import _extract_first_json_object


class OllamaClientTests(unittest.TestCase):
    def test_extract_first_json_object_from_wrapped_content(self) -> None:
        payload = _extract_first_json_object(
            'Some preface\n{"summary":"ok","keywords":["a","b"]}\nSome suffix'
        )
        self.assertEqual(payload["summary"], "ok")
        self.assertEqual(payload["keywords"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
