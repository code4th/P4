from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts import p2_loop_worker
from scripts import p2_watchdog
from scripts.p2_watchdog import HealthMonitor, _find_conflicting_dashboard_pids, _terminate_conflicting_dashboards
from p2_core.workspace import write_json


class WatchdogTests(unittest.TestCase):
    def test_progress_timeout_budget_extends_validation(self) -> None:
        with mock.patch.object(p2_loop_worker, "CHILD_NO_PROGRESS_TIMEOUT_SECONDS", 120.0):
            budget = p2_loop_worker._progress_timeout_budget({"current_action": "run_validation", "phase": "acting"})
        self.assertEqual(budget, 240.0)

    def test_child_timeout_reason_prefers_max_attempt_timeout(self) -> None:
        with mock.patch.object(p2_loop_worker, "CHILD_MAX_ATTEMPT_SECONDS", 240.0), mock.patch.object(
            p2_loop_worker, "CHILD_NO_PROGRESS_TIMEOUT_SECONDS", 120.0
        ):
            reason = p2_loop_worker._child_timeout_reason(
                started_at=0.0,
                last_progress_at=200.0,
                now=240.0,
                runtime={"phase": "acting", "current_action": "apply_patch"},
            )
        self.assertEqual(reason, "worker max-attempt timeout after 240s")

    def test_child_timeout_reason_uses_no_progress_timeout_with_phase_and_action(self) -> None:
        with mock.patch.object(p2_loop_worker, "CHILD_MAX_ATTEMPT_SECONDS", 900.0), mock.patch.object(
            p2_loop_worker, "CHILD_NO_PROGRESS_TIMEOUT_SECONDS", 120.0
        ):
            reason = p2_loop_worker._child_timeout_reason(
                started_at=0.0,
                last_progress_at=0.0,
                now=121.0,
                runtime={"phase": "acting", "current_action": "search_code"},
            )
        self.assertEqual(reason, "worker no-progress timeout after 120s (phase=acting, action=search_code)")

    def test_worker_fresh_allows_long_running_active_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "status.json"
            write_json(
                state_path,
                {
                    "status": "running",
                    "last_loop_started_at": (datetime.now(UTC) - timedelta(minutes=8)).isoformat(),
                    "worker_heartbeat_at": None,
                },
            )
            monitor = HealthMonitor(root / "monitor.log", state_path=state_path)
            self.assertTrue(monitor.worker_fresh(max_age_seconds=120, running_grace_seconds=1800))

    def test_worker_fresh_rejects_stale_idle_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "status.json"
            write_json(
                state_path,
                {
                    "status": "idle",
                    "last_loop_finished_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                    "worker_heartbeat_at": None,
                },
            )
            monitor = HealthMonitor(root / "monitor.log", state_path=state_path)
            self.assertFalse(monitor.worker_fresh(max_age_seconds=120, running_grace_seconds=1800))

    def test_write_worker_shutdown_marker_includes_runtime_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "status.json"
            marker_path = root / "worker_shutdown.json"
            write_json(
                state_path,
                {
                    "current_candidate_id": "c0042",
                    "current_action": "run_validation",
                    "current_action_step": 3,
                },
            )
            with mock.patch.object(p2_watchdog, "STATE_PATH", state_path), mock.patch.object(
                p2_watchdog, "WORKER_SHUTDOWN_MARKER_PATH", marker_path
            ):
                p2_watchdog._write_worker_shutdown_marker(
                    reason="code update restart",
                    phase="watchdog_stop",
                    note="watchdog が worker を停止しました。",
                )
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["candidate_id"], "c0042")
            self.assertEqual(payload["current_action"], "run_validation")
            self.assertEqual(payload["current_action_step"], 3)
            self.assertEqual(payload["reason"], "code update restart")

    @mock.patch("scripts.p2_watchdog.subprocess.check_output")
    def test_find_conflicting_dashboard_pids_matches_same_root_and_port(self, mock_check_output: mock.Mock) -> None:
        mock_check_output.return_value = "\n".join(
            [
                "100 /usr/bin/python3 -m p2_core.cli --root /tmp/demo dashboard --host 127.0.0.1 --port 8897",
                "101 /usr/bin/python3 -m p2_core.cli --root /tmp/other dashboard --host 127.0.0.1 --port 8897",
                "102 /usr/bin/python3 -m p2_core.cli --root /tmp/demo dashboard --host 127.0.0.1 --port 9900",
            ]
        )
        pids = _find_conflicting_dashboard_pids(root=Path("/tmp/demo"), host="127.0.0.1", port=8897)
        self.assertEqual(pids, [100])

    @mock.patch("scripts.p2_watchdog.time.sleep", return_value=None)
    @mock.patch("scripts.p2_watchdog.os.kill")
    @mock.patch("scripts.p2_watchdog._find_conflicting_dashboard_pids")
    def test_terminate_conflicting_dashboards_skips_keep_pid(
        self,
        mock_find: mock.Mock,
        mock_kill: mock.Mock,
        _mock_sleep: mock.Mock,
    ) -> None:
        mock_find.side_effect = [[100, 101], []]
        killed = _terminate_conflicting_dashboards(
            root=Path("/tmp/demo"),
            host="127.0.0.1",
            port=8897,
            keep_pid=101,
        )
        self.assertEqual(killed, [100])
        mock_kill.assert_called_once()
        self.assertEqual(mock_kill.call_args.args[0], 100)


if __name__ == "__main__":
    unittest.main()
