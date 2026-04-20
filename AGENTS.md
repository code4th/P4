# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

OpenClaw is a multi-module research system centered on P1, an autonomous growth kernel for LLM-based self-improvement. P1 operates as a continuous loop: observation → knowledge → proposal → evaluation → governance → action. OpenClaw serves as a temporary runtime/control plane — P1's judgment, governance, and rollback stay external to OpenClaw.

## Repository Layout

- **p1-core/** — Primary autonomous agent: growth loop, autonomy runtime, governance, knowledge stores, CLI, dashboard
- **artificial-life/** — Artificial life simulation sandbox (individuals, resources, traits, cooperation/competition)
- **subjectivity-sandbox/** — Observable subject-like property research (lineages A-D, metrics, sweep experiments)
- **keeper_adapter/** — Bridge between OpenClaw runtime and P1 reporting/intervention
- **social-agent/** — Social structure analysis agent with memory and reports
- **reviewer-agent/** — Code review agent with continuous memory
- **handoff/** — Architectural handoff documents and design notes; `P1_MASTER.md` is the single source of truth for P1 design intent

## Commands

All modules use Python 3 with `unittest`. No external build system or package manager.

### Running tests

```bash
# p1-core (primary)
cd p1-core && python3 -m unittest discover -s tests

# artificial-life
cd artificial-life && python3 -m unittest discover -s tests

# subjectivity-sandbox
cd subjectivity-sandbox && python3 -m unittest discover -s tests

# keeper_adapter
cd keeper_adapter && python3 -m unittest discover -s tests

# Single test file
python3 -m unittest tests.test_autonomy

# Single test method
python3 -m unittest tests.test_autonomy.TestAutonomy.test_method_name
```

### Running P1

```bash
cd p1-core

# Bootstrap a P1 workspace
python3 -m p1_core.bootstrap.bootstrap_p1 --root ~/.openclaw/workspace/systems/p1 --force

# Dashboard
python3 -m p1_core.cli --root ~/.openclaw/workspace/systems/p1 dashboard

# Chat
~/.openclaw/workspace/systems/p1/bin/p1-agent chat --new-session --message "hello P1"
```

### Running sandboxes

```bash
# Artificial life experiment
cd artificial-life && python3 -m artificial_life.cli --experiment A --steps 40 --seed 7

# Subjectivity sweep
cd subjectivity-sandbox && python3 -m subjectivity_sandbox.sweep
```

## Architecture

### P1-Core internals

- **`p1_core/core/`** — Core subsystems: autonomy runtime (`autonomy.py`), action runtime, LLM routing, knowledge/proposal/governance/policy stores, MetaAgent (self-modification), evaluator, governor, critic
- **`p1_core/pipeline/growth_loop.py`** — Main growth loop orchestration (~33KB): ingests observations, extracts lessons via local LLM, generates proposals, evaluates via cloud LLM
- **`p1_core/worker/`** — Local LLM worker layer (Ollama client and service)
- **`p1_core/adapters/`** — OpenClaw integration boundary (text/action backends)
- **`p1_core/bootstrap/`** — Workspace scaffolding, agent registration, config patch generation
- **`p1_core/cli.py`** — CLI entry point; **`dashboard.py`** — Web-based autonomy dashboard

### Key design rules

- Single source of truth for runtime coordination: `state/autonomy/runtime-state.json`
- Local LLM (Ollama) for cheap auxiliary cognition; cloud LLM (OpenClaw) for high-quality judgment
- All state transitions logged for audit/rollback; proposals require governance approval before execution
- Purpose-first: if a tool choice conflicts with P1's intended purpose, the purpose wins
- OpenClaw is a disposable control plane — governance and rollback must never be locked inside it

### State files (at P1 workspace root)

- `state/autonomy/runtime-state.json` — Runtime coordination
- `state/knowledge/knowledge.jsonl` — Growth loop event log
- `state/proposals/` — Proposal candidates and evaluations
- `state/governance/` — Policy and approval rules
- `state/reports/daily/` — Daily reports for keeper bridge

### Dependencies

Pure Python stdlib (`json`, `pathlib`, `subprocess`, `dataclasses`, `urllib`). External runtime dependency: Ollama server for local LLM inference.
