from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from p2_core.workspace import WorkspacePaths, bootstrap_workspace, read_json


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_creates_seed_runtime_and_working_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p2-demo"
            payload = bootstrap_workspace(root)
            paths = WorkspacePaths(root)

            self.assertTrue(payload["ok"])
            self.assertTrue(paths.seed_initial_version_dir.exists())
            self.assertTrue((paths.runtime_versions_dir / "v0001" / "agent" / "goal_logic.py").exists())
            self.assertTrue(paths.goal_path.exists())
            self.assertTrue(paths.self_model_path.exists())
            self.assertTrue(paths.version_path.exists())
            self.assertTrue(paths.system_skills_path.exists())
            self.assertTrue(paths.memos_path.exists())
            self.assertEqual(read_json(paths.version_path)["active_generation"], 1)
            self.assertEqual(read_json(paths.goal_path)["status"], "active")
            self_model = read_json(paths.self_model_path)
            self.assertEqual(self_model["editable_zones"], ["agent/goal_logic.py"])
            self.assertEqual(self_model["editable_zone_specs"][0]["zone_id"], "agent_goal_logic")
            self.assertEqual(self_model["editable_zone_specs"][1]["zone_id"], "operator_runtime_loop")
            self.assertEqual(self_model["editable_zone_specs"][1]["scope"], "operator_runtime")
            self.assertFalse(self_model["editable_zone_specs"][1]["selection_enabled"])
            self.assertIn("delta_context_update", self_model["editable_zone_specs"][1]["allowed_regions"])
            self.assertGreaterEqual(len(read_json(paths.system_skills_path)), 3)
            self.assertEqual(paths.memos_path.read_text(encoding="utf-8"), "")

            proc = subprocess.run(
                ["python3", "-m", "unittest", "discover", "-s", "tests"],
                cwd=paths.runtime_versions_dir / "v0001",
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
