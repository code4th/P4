from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.dashboard import dashboard_snapshot, render_dashboard_html
from p1_core.autonomy import AutonomyRuntime


class DashboardTests(unittest.TestCase):
    def test_dashboard_snapshot_includes_history_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=object())
            runtime.ticks_path.parent.mkdir(parents=True, exist_ok=True)
            runtime.ticks_path.write_text(
                '{"timestamp":"2026-04-05T10:00:00+09:00","status":"replied","summary":"replied via local","reply":{"message_id":"message-1"},"next_wake_at":"2026-04-05T10:05:00+09:00"}\n',
                encoding="utf-8",
            )
            metaagent_path = root / "state" / "metaagent" / "generation_history.json"
            metaagent_path.parent.mkdir(parents=True, exist_ok=True)
            metaagent_path.write_text(
                json.dumps(
                    {
                        "history": [
                            {
                                "timestamp": "2026-04-05T10:10:00+09:00",
                                "target_name": "thought_policy.py",
                                "message": "applied proposed file revision",
                                "success": True,
                            }
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = dashboard_snapshot(root)
            html = render_dashboard_html(snapshot)

        self.assertEqual(snapshot["state"]["mode"], "cooperative_tick")
        self.assertEqual(snapshot["history"][0]["thought"], "replied via local")
        self.assertEqual(snapshot["history"][0]["executed"]["type"], "reply")
        self.assertIn("P1 ダッシュボード", html)
        self.assertIn("自律履歴", html)
        self.assertIn("起床まで", html)
        self.assertIn("トリガー種別", html)
        self.assertIn("最近の initiative", html)
        self.assertIn("最近の self-repair", html)
        self.assertIn("EventSource", html)
        self.assertIn("replied via local", html)
        self.assertIn("thought_policy.py", html)
        self.assertNotIn("refreshSnapshot", html)
        self.assertNotIn("meta http-equiv", html)


if __name__ == "__main__":
    unittest.main()
