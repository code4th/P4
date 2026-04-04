from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.worker.service import WorkerService


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return self.payload


class WorkerServiceTests(unittest.TestCase):
    def test_summarize_logs_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = WorkerService(
                llm_client=FakeClient({"summary": "short", "keywords": ["timeout"]}),
                log_dir=Path(tmp),
            )
            payload = service.summarize({"text": "timeout happened again"})
            self.assertTrue(payload["ok"])
            files = list(Path(tmp).glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            logged = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(logged[0]["endpoint"], "summarize")

    def test_classify_requires_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = WorkerService(llm_client=FakeClient({}), log_dir=Path(tmp))
            with self.assertRaises(ValueError):
                service.classify({"text": ""})

    def test_draft_lessons_keeps_counterexamples_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = WorkerService(
                llm_client=FakeClient(
                    {
                        "lessons": ["timeouts clustered around one tool"],
                        "counterexamples": ["some runs succeeded under same load"],
                        "follow_up_questions": ["what changed in retry policy?"],
                    }
                ),
                log_dir=Path(tmp),
            )
            payload = service.draft_lessons({"text": "mixed outcome log"})
            self.assertIn("counterexamples", payload["result"])


if __name__ == "__main__":
    unittest.main()
