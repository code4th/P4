from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.bootstrap.apply_openclaw_config_patch import apply_patch, rollback_patch
from p1_core.bootstrap.bootstrap_p1 import scaffold_workspace
from p1_core.bootstrap.generate_openclaw_config_patch import generate_patch


class OpenClawApplyPatchTests(unittest.TestCase):
    def test_apply_and_rollback_patch_updates_temp_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_root = root / "workspace" / "systems" / "p1"
            scaffold_workspace(workspace_root)
            generate_patch(openclaw_home=root / ".openclaw", workspace_root=workspace_root, agent_name="p1")

            config_path = root / ".openclaw" / "openclaw.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps({"agents": {"list": [{"id": "main"}, {"id": "analysis"}]}}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            applied = apply_patch(config_path=config_path, workspace_root=workspace_root, agent_name="p1")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(applied["changed"])
            self.assertIn("p1", [item["id"] for item in config["agents"]["list"]])

            rolled_back = rollback_patch(config_path=config_path, agent_name="p1")
            rolled = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(rolled_back["changed"])
            self.assertNotIn("p1", [item["id"] for item in rolled["agents"]["list"]])


if __name__ == "__main__":
    unittest.main()
