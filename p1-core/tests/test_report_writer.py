from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.reporting.report_writer import ReportWriter


class ReportWriterTests(unittest.TestCase):
    def test_writer_emits_keeper_adapter_compatible_glance_and_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ReportWriter(Path(tmp))
            glance_path = writer.write_glance(
                status="healthy",
                main_points=["p1 point"],
                recommended_interventions=["keep boundary"],
                track_summary={"worker": "healthy"},
                approval_pending=[{"type": "policy_change"}],
                date="2026-04-04",
            )
            health_path = writer.write_health(
                status="healthy",
                approval_pending=[{"type": "policy_change"}],
            )
            glance = json.loads(glance_path.read_text(encoding="utf-8"))
            health = json.loads(health_path.read_text(encoding="utf-8"))
            self.assertEqual(glance["mainPoints"][0], "p1 point")
            self.assertEqual(glance["tuningSummary"]["approvalPending"][0]["type"], "policy_change")
            self.assertEqual(health["approvalPending"][0]["type"], "policy_change")

    def test_writer_emits_daily_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ReportWriter(Path(tmp))
            daily_path = writer.write_daily(
                status="bootstrapping",
                summary="summary",
                sections=[{"title": "Section", "points": ["point"]}],
                proposals=[{"id": "p1", "state": "pending_approval"}],
                date="2026-04-04",
            )
            daily = json.loads(daily_path.read_text(encoding="utf-8"))
            self.assertEqual(daily["summary"], "summary")
            self.assertEqual(daily["proposals"][0]["state"], "pending_approval")


if __name__ == "__main__":
    unittest.main()
