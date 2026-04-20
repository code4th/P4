from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p3_core.benchmark import run_benchmark_suite


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_returns_ranking_and_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = run_benchmark_suite(root, models=[])
            self.assertTrue(payload["ok"])
            self.assertIn("ranking", payload)
            self.assertIn("recommended_next_target", payload)
            self.assertIn("priority", payload["recommended_next_target"])
            self.assertEqual(payload["models"], [])
            status = json.loads((root / "state" / "runtime" / "benchmark.json").read_text(encoding="utf-8"))
            self.assertIn("cases", status)


if __name__ == "__main__":
    unittest.main()
