from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.bootstrap.bootstrap_p1 import scaffold_workspace
from p1_core.bootstrap.install_openclaw_agent import install_openclaw_agent


class OpenClawInstallTests(unittest.TestCase):
    def test_install_openclaw_agent_creates_p1_slot_from_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openclaw_home = root / ".openclaw"
            source_agent = openclaw_home / "agents" / "main" / "agent"
            source_agent.mkdir(parents=True, exist_ok=True)
            (source_agent / "models.json").write_text(
                json.dumps({"providers": {"ollama": {"models": [{"id": "qwen3:4b-instruct"}]}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (source_agent / "auth-profiles.json").write_text(
                json.dumps({"profiles": {"ollama:default": {"provider": "ollama"}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            workspace_root = root / "workspace" / "systems" / "p1"
            scaffold_workspace(workspace_root)

            created = install_openclaw_agent(
                openclaw_home=openclaw_home,
                workspace_root=workspace_root,
                agent_name="p1",
                source_agent="main",
            )

            self.assertTrue((openclaw_home / "agents" / "p1" / "agent" / "models.json").exists())
            self.assertTrue((openclaw_home / "agents" / "p1" / "agent" / "auth-profiles.json").exists())
            self.assertTrue((openclaw_home / "agents" / "p1" / "agent" / "p1-openclaw-entry.json").exists())
            self.assertTrue((openclaw_home / "agents" / "p1" / "sessions" / "sessions.json").exists())
            entry = json.loads((openclaw_home / "agents" / "p1" / "agent" / "p1-openclaw-entry.json").read_text(encoding="utf-8"))
            self.assertEqual(entry["display_name"], "P1")
            self.assertEqual(entry["transport_entrypoint"], str(workspace_root / "bin" / "p1-agent"))
            self.assertGreaterEqual(len(created), 4)


if __name__ == "__main__":
    unittest.main()
