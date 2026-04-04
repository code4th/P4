from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.bootstrap.bootstrap_p1 import scaffold_workspace
from p1_core.bootstrap.generate_openclaw_config_patch import generate_patch


class OpenClawConfigPatchTests(unittest.TestCase):
    def test_generate_patch_writes_agent_entry_and_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_root = root / "workspace" / "systems" / "p1"
            scaffold_workspace(workspace_root)
            created = generate_patch(
                openclaw_home=root / ".openclaw",
                workspace_root=workspace_root,
                agent_name="p1",
            )

            entry_path = workspace_root / "agent" / "openclaw-config-agent-entry.json"
            guide_path = workspace_root / "agent" / "openclaw-config-apply.md"
            self.assertIn(entry_path, created)
            self.assertIn(guide_path, created)

            entry = json.loads(entry_path.read_text(encoding="utf-8"))
            self.assertEqual(entry["id"], "p1")
            self.assertEqual(entry["workspace"], str(workspace_root))
            self.assertEqual(entry["agentDir"], str(root / ".openclaw" / "agents" / "p1" / "agent"))
            self.assertNotIn("metadata", entry)
            self.assertIn("agents.list", guide_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
