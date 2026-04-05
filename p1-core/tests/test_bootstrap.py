from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.bootstrap.bootstrap_p1 import scaffold_workspace


class BootstrapTests(unittest.TestCase):
    def test_scaffold_workspace_creates_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p1"
            scaffold_workspace(root)
            self.assertTrue((root / "profile.json").exists())
            self.assertTrue((root / "config.json").exists())
            self.assertTrue((root / "prompt.md").exists())
            self.assertTrue((root / "runbook.md").exists())
            self.assertTrue((root / "agent" / "manifest.json").exists())
            self.assertTrue((root / "agent" / "openclaw-agent.md").exists())
            self.assertTrue((root / "bin" / "p1").exists())
            self.assertTrue((root / "bin" / "p1-agent").exists())
            self.assertTrue((root / "bin" / "p1-worker").exists())
            self.assertTrue((root / "state" / "conversation").exists())
            self.assertTrue((root / "state" / "world").exists())
            self.assertTrue((root / "state" / "governance").exists())
            self.assertTrue((root / "state" / "experiments").exists())
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "agent" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(config["promotion_mode"], "proposal_only")
            self.assertEqual(config["workspace_kind"], "openclaw-system-agent")
            self.assertEqual(config["autonomy"]["mode"], "cooperative_tick")
            self.assertTrue(config["autonomy"]["local_first"])
            self.assertFalse(config["openclaw_backend"]["enabled"])
            self.assertIn("run_command", config["openclaw_backend"]["commands"])
            self.assertEqual(manifest["entrypoint"]["wrapper"], "bin/p1-agent")


if __name__ == "__main__":
    unittest.main()
