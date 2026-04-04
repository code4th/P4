# P1 Master Document

Date: 2026-04-04
Status: Single source of truth for P1 external-core work

## 1. Purpose

This project is not primarily about building an AI framework.

Its top goal is to build a core loop through which an LLM can sustain long-term autonomous growth via:

- observation
- knowledge formation
- critique
- proposal
- evaluation
- governance
- self-modification

The intended long-term direction is an artificial-life-like agent substrate that is:

- runtime-independent
- model-independent
- OpenClaw-independent
- split between local-LLM auxiliary cognition and cloud-LLM high-quality judgment
- focused on growth in how knowledge is handled, not only on knowledge accumulation
- governed across short and long time scales
- auditable, comparable, and rollbackable in its self-modification

## 2. Non-Negotiable Direction

These are fixed and should not drift:

- OpenClaw is a temporary execution substrate
- OpenClaw must not become the growth kernel
- the core logic stays outside OpenClaw
- OpenClaw is control plane, not identity
- P1 is an independent individual that uses OpenClaw
- local LLMs are auxiliary cognition
- cloud LLMs are for final/high-stakes judgment
- the first self-modified target is operational rules, not model weights
- all changes must remain comparable, reviewable, and rollbackable

Not adopted at this stage:

- full custom framework first
- deep OpenClaw surgery first
- frequent base-model weight updates
- unconstrained autonomous free modification
- internal OpenClaw agent spawning as the foundation

## 3. What P1 Is

P1 is not a persona living inside OpenClaw.

P1 is an independent growth agent with:

- its own conversation identity in the future
- an external core outside OpenClaw
- access to a local LLM auxiliary brain
- knowledge-state management
- critique logs
- operational rule change proposals
- long-term potential to become the central self-growing individual

## 4. Responsibility Split

OpenClaw owns:

- I/O
- tool execution
- OS interaction
- runtime robustness
- transport and presentation

P1 external core owns:

- knowledge state
- policy / critic / proposer / evaluator / governor
- cross-track judgment
- pre-approval review
- compression of research and operational outcomes

Hard prohibition:

- do not grow an independent policy engine inside OpenClaw-side adapter code
- do not re-implement P1 judgment inside `keeper_adapter`

## 5. Current Architecture

### 5.1 Main Components

- `p1-core/`
  - external core workspace
- `keeper_adapter/`
  - thin OpenClaw-side bridge
- `handoff/`
  - planning, constraints, operating rules, and supporting notes

### 5.2 Worker

Local Ollama-backed HTTP worker:

- `/summarize`
- `/classify`
- `/draft_lessons`
- `/health`

Key files:

- [ollama_worker.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/worker/ollama_worker.py)
- [service.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/worker/service.py)
- [ollama_client.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/worker/ollama_client.py)

### 5.3 Bootstrap

External workspace scaffolding:

- `profile.json`
- `config.json`
- `prompt.md`
- `runbook.md`
- `state/reports/`
- `state/knowledge/`
- `state/policies/`
- `state/proposals/`
- `state/archive/`
- `logs/`

Key file:

- [bootstrap_p1.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/bootstrap/bootstrap_p1.py)

### 5.4 Core Modules

- event log
- knowledge store
- policy engine
- critic
- proposer
- evaluator
- governor
- proposal store

Key files:

- [knowledge_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/knowledge_store.py)
- [proposal_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/proposal_store.py)
- [policy_engine.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/policy_engine.py)
- [critic.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/critic.py)
- [evaluator.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/evaluator.py)
- [governor.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/governor.py)

### 5.5 Growth Loop

The current minimal growth loop already performs:

- lesson extraction
- classification
- summarization
- candidate knowledge persistence
- state transitions across `candidate / deferred / active / retired`
- proposal snapshot creation
- proposal comparison against previous snapshot
- governance review
- rollback of proposal snapshots
- report generation for bridge consumption

Key file:

- [growth_loop.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/pipeline/growth_loop.py)

Outputs:

- `state/knowledge/knowledge.jsonl`
- `state/events/event-log.jsonl`
- `state/proposals/latest-proposals.json`
- `state/proposals/snapshots/*.json`
- `state/reports/daily/*-glance.json`
- `state/reports/daily/*-daily.json`
- `state/health.json`

## 6. Current State Logic

Knowledge states:

- `raw`
- `candidate`
- `deferred`
- `active`
- `retired`

Current minimal routing:

- contradicted or high-impact proposal -> `deferred`
- new bounded proposal without counterexamples -> `active`
- obsolete or duplicate-against-previous-snapshot proposal -> `retired`

This is still heuristic and not the final governance quality target.

## 7. What Counts As Core Completion

P1 core is **not** complete merely because it can propose and compare.

For this project, core completion means all of the following:

- observation, knowledge formation, critique, proposal, evaluation, governance, and self-modification candidate handling all close inside the external core
- knowledge state is managed across `raw / candidate / deferred / active / retired`
- proposals and knowledge state are comparable, auditable, and rollbackable
- high-risk changes are approval-gated
- low-risk improvements can be executed autonomously
- small experiments can be run by the core itself
- experiment outcomes feed back into later updates

Therefore, the current state is:

- minimal external core skeleton: complete
- autonomous self-improvement core: not yet complete

## 8. Practical Operations Today

Current operational entrypoints:

1. Read P1 status
   - `cd /Users/satojunichi/Documents/openclaw`
   - `python3 -m keeper_adapter.cli status`

2. Read detailed report
   - `python3 -m keeper_adapter.cli report --kind daily`

3. Read approval-pending items
   - `python3 -m keeper_adapter.cli approvals`

4. Advance P1 core
   - `cd /Users/satojunichi/Documents/openclaw/p1-core`
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --input-text "example observation"`

5. Roll back proposal state
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --rollback-snapshot-id 2026-04-04-proposals`

Current limitation:

- there is no full direct chat-style P1 operator interface yet
- today, P1 is operated through bridge commands and growth-loop execution

## 9. Rollback Principles

Always preserve:

- logs
- counterexamples
- deferred items
- comparison trails

Operational rollback:

1. stop the worker
2. restore a proposal snapshot
3. confirm `latest-proposals.json` points at the restored snapshot
4. confirm `keeper_adapter.cli status` and `report --kind daily` reflect rollback state
5. archive failed artifacts only after restored state is confirmed

## 10. Verified So Far

Verified in implementation:

- `p1-core` unit tests passing
- bootstrap scaffolding works
- worker contract works
- growth loop writes knowledge / proposal / report / event outputs
- `deferred` transitions work
- `active` promotion works for bounded counterexample-free proposals
- `retired` works for obsolete and duplicate-against-previous-snapshot proposals
- governance review is written into snapshots and daily reports
- rollback updates proposal latest pointer and bridge-visible state
- `keeper_adapter` reads `glance / daily / approvals` from generated outputs

## 11. Remaining Work

The next meaningful work is no longer basic skeleton building. It is quality and autonomy work:

1. connect cloud-side evaluation to approval and promotion decisions
2. add rollback snapshots for knowledge-state layer itself
3. connect evaluator / governor decisions to longer-horizon governance rules
4. split reporting into short-term and long-term governance layers
5. run end-to-end verification against a real Ollama worker
6. add an experiment layer for low-risk external actions
7. let low-risk improvements run autonomously while keeping high-risk changes approval-gated

## 12. Supporting Documents

These are now supporting docs, not the primary entrypoint:

- [p1-manager-handoff-source-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-manager-handoff-source-2026-04-04.md)
- [p1-canonical-handoff-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-canonical-handoff-2026-04-04.md)
- [p1-external-core-plan-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-external-core-plan-2026-04-04.md)
- [p1-openclaw-bridge-spec-2026-03-30.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-bridge-spec-2026-03-30.md)
- [p1-openclaw-operating-rule-2026-03-29.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-operating-rule-2026-03-29.md)
- [p1-keeper-handoff-2026-03-29.md](/Users/satojunichi/Documents/openclaw/handoff/p1-keeper-handoff-2026-03-29.md)
- [p1-bootstrap-runbook.md](/Users/satojunichi/Documents/openclaw/p1-core/runbooks/p1-bootstrap-runbook.md)
