# P1 Core

P1 Core is the external growth kernel for P1.

Design intent:

- keep OpenClaw as a disposable control plane
- keep P1 judgment and governance outside OpenClaw
- use a local LLM worker for cheap auxiliary cognition
- preserve logs, state transitions, and rollback points

Included in this first skeleton:

- local Ollama worker with HTTP JSON endpoints
- bootstrap scaffolder for a standalone P1 workspace
- report writer compatible with the current `keeper_adapter` contract
- minimal growth loop that persists candidate knowledge and proposal snapshots
- minimal conversation surface and transcript store
- world observation and bounded action request stores
- local dashboard for inspecting autonomy history, tasks, and gaps
- minimal external core package layout
- OpenClaw adapter boundary note

Front-door intent:

- `bin/p1 chat` is the temporary front door for talking to P1 as a distinct individual
- `bin/p1-agent` is the OpenClaw-facing wrapper for treating P1 as a dedicated agent surface
- `install_openclaw_agent.py` scaffolds `~/.openclaw/agents/p1` without moving P1 judgment into OpenClaw
- `generate_openclaw_config_patch.py` writes a safe agent registration patch instead of editing `openclaw.json` directly
- OpenClaw remains a runtime/control plane, not P1's identity
- governance, audit, and rollback stay in the external core behind that interface

Quick start:

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m unittest discover -s tests
python3 -m p1_core.worker.ollama_worker --help
python3 -m p1_core.bootstrap.bootstrap_p1 --help
python3 -m p1_core.cli --root /Users/satojunichi/.openclaw/workspace/systems/p1 dashboard
python3 -m p1_core.reporting.write_example_reports --root /tmp/p1-core-smoke
```

OpenClaw-side P1 individual:

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m p1_core.bootstrap.bootstrap_p1 --root /Users/satojunichi/.openclaw/workspace/systems/p1 --force
/Users/satojunichi/.openclaw/workspace/systems/p1/bin/p1-agent status
/Users/satojunichi/.openclaw/workspace/systems/p1/bin/p1-agent chat --new-session --message "hello P1"
python3 -m p1_core.bootstrap.install_openclaw_agent --openclaw-home /Users/satojunichi/.openclaw --workspace-root /Users/satojunichi/.openclaw/workspace/systems/p1 --agent-name p1 --source-agent main
python3 -m p1_core.bootstrap.generate_openclaw_config_patch --openclaw-home /Users/satojunichi/.openclaw --workspace-root /Users/satojunichi/.openclaw/workspace/systems/p1 --agent-name p1
```
