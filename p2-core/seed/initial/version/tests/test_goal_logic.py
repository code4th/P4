from __future__ import annotations

import subprocess
import unittest

from agent.goal_logic import describe_agent, render_operator_message, self_check


class SelfImprovementAgentTests(unittest.TestCase):
    def test_describe_agent_has_core_fields(self) -> None:
        payload = describe_agent()
        self.assertTrue(payload["agent_name"])
        self.assertIn(payload["stream_style"], {"plain", "structured", "rich"})
        self.assertGreaterEqual(len(payload["operator_guidance"]), 2)

    def test_operator_message_mentions_self_improvement(self) -> None:
        self.assertIn("自己改善", render_operator_message())

    def test_self_check_succeeds(self) -> None:
        self.assertEqual(self_check(), 0)

    def test_cli_check_mode_succeeds(self) -> None:
        proc = subprocess.run(
            ["python3", "agent/goal_logic.py", "--check"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
