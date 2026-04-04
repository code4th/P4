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
- minimal external core package layout
- OpenClaw adapter boundary note

Quick start:

```bash
cd /Users/satojunichi/Documents/openclaw/p1-core
python3 -m unittest discover -s tests
python3 -m p1_core.worker.ollama_worker --help
python3 -m p1_core.bootstrap.bootstrap_p1 --help
python3 -m p1_core.reporting.write_example_reports --root /tmp/p1-core-smoke
```
