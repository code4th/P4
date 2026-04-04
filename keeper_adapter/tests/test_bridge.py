from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from keeper_adapter.bridge import approvals, classify_command, keeper_status


class KeeperBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        daily_dir = self.root / "state" / "reports" / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        (daily_dir / "2026-03-30-glance.json").write_text(json.dumps({
            "status": "degraded",
            "mainPoints": ["p1 main point"],
            "recommendedInterventions": ["do bounded thing"],
            "trackSummary": {"liveDegraded": 1},
            "tuningSummary": {"approvalPending": [{"type": "high_risk_change"}]},
        }))
        (self.root / "state" / "health.json").write_text(json.dumps({
            "approvalPending": [{"type": "fallback_high_risk"}]
        }))
        self.previous = os.environ.get("OPENCLAW_P1_ROOT")
        os.environ["OPENCLAW_P1_ROOT"] = str(self.root)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("OPENCLAW_P1_ROOT", None)
        else:
            os.environ["OPENCLAW_P1_ROOT"] = self.previous
        self.temp_dir.cleanup()

    def test_status_reads_latest_glance(self) -> None:
        payload = keeper_status()
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["mainPoints"][0], "p1 main point")

    def test_approvals_prefers_report_pending(self) -> None:
        payload = approvals()
        self.assertEqual(payload["pending"][0]["type"], "high_risk_change")

    def test_risk_classification(self) -> None:
        self.assertEqual(classify_command("skepticism up").risk_tier, "bounded_operational")
        self.assertEqual(classify_command("rollback last_change").risk_tier, "approval_required")


if __name__ == "__main__":
    unittest.main()
