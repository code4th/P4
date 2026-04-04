# P1 Master Document

Date: 2026-04-04
Status: Single source of truth for P1 external-core work

Main-thread catch-up:

- [p1-main-thread-catchup-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-main-thread-catchup-2026-04-04.md)

Interface correction:

- the currently implemented external-core operator surface is useful, but it is not the final intended front-end
- the desired day-to-day interface is an OpenClaw-visible separate P1 agent, with the external core behind it as memory, governance, and rollback substrate

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
- a minimal conversation surface through the external core
- explicit world-observation and world-action interfaces
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

## 4.5 Interface Direction Correction

The external-core-first implementation was useful as a bootstrap path, but the intended operator experience is different.

The desired steady-state shape is:

- P1 appears as a separate agent interface on the OpenClaw side
- P1 uses LLM-backed reasoning as its main conversational and operational loop
- OpenClaw is used as the practical runtime surface for that agent
- the external core remains the place for memory, governance, audit, comparison, and rollback

Therefore:

- `p1-core` should be treated as the institutional substrate behind P1
- OpenClaw-facing P1 interface work is now first-class, not optional polish
- local worker infrastructure is auxiliary, not the final identity of P1

## 5. Current Architecture

### 5.1 Main Components

- `p1-core/`
  - external core workspace
- `keeper_adapter/`
  - thin OpenClaw-side bridge
- `handoff/`
  - planning, constraints, operating rules, and supporting notes

Current interpretation:

- `p1-core/` is already a viable governance and memory substrate
- the next main-thread implementation focus should be the OpenClaw-facing P1 agent interface
- `bin/p1` is only a temporary operator wrapper, not the final UX target

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
- `state/conversation/`
- `state/world/`
- `state/archive/`
- `logs/`

Key file:

- [bootstrap_p1.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/bootstrap/bootstrap_p1.py)

### 5.4 Core Modules

- event log
- knowledge store
- policy engine
- policy store
- critic
- proposer
- evaluator
- governor
- governance store
- experiment runner
- proposal store

Key files:

- [knowledge_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/knowledge_store.py)
- [proposal_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/proposal_store.py)
- [policy_engine.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/policy_engine.py)
- [policy_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/policy_store.py)
- [critic.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/critic.py)
- [evaluator.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/evaluator.py)
- [governor.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/governor.py)
- [governance_store.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/governance_store.py)
- [experiment_runner.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/core/experiment_runner.py)

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
- policy-state application and rollback
- governance-profile loading
- bounded autonomous experiment execution
- report generation for bridge consumption

Key file:

- [growth_loop.py](/Users/satojunichi/Documents/openclaw/p1-core/p1_core/pipeline/growth_loop.py)

Outputs:

- `state/knowledge/knowledge.jsonl`
- `state/events/event-log.jsonl`
- `state/proposals/latest-proposals.json`
- `state/proposals/snapshots/*.json`
- `state/policies/latest-policy.json`
- `state/policies/snapshots/*.json`
- `state/governance/latest-governance.json`
- `state/experiments/latest-experiment.json`
- `state/experiments/actions/*.json`
- `state/conversation/transcript.jsonl`
- `state/world/observations.jsonl`
- `state/world/action-requests.jsonl`
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
- the agent can converse through the external core
- the agent can observe and request bounded action toward the external world

Therefore, the current state is:

- minimal external core skeleton: complete
- bounded autonomous self-improvement core: complete as a minimum operational loop

## 8. Practical Operations Today

Current operational entrypoints:

1. Read P1 status
   - `cd /Users/satojunichi/Documents/openclaw`
   - `python3 -m keeper_adapter.cli status`
   - or `cd /Users/satojunichi/Documents/openclaw/p1-core && python3 -m p1_core.cli status`
   - or `/Users/satojunichi/.openclaw/workspace/systems/p1/bin/p1 status`

2. Read detailed report
   - `python3 -m keeper_adapter.cli report --kind daily`

3. Read approval-pending items
   - `python3 -m keeper_adapter.cli approvals`
   - or `python3 -m p1_core.cli approvals`

4. Advance P1 core
   - `cd /Users/satojunichi/Documents/openclaw/p1-core`
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --input-text "example observation"`
   - or `python3 -m p1_core.cli ingest --model qwen3:4b-instruct --input-text "example observation"`

5. Roll back proposal state
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --rollback-snapshot-id 2026-04-04-proposals`

6. Roll back policy state
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --rollback-policy-snapshot-id baseline-policy`

7. Inspect unified external-core state
   - `python3 -m p1_core.cli state`

8. Talk with P1
   - `python3 -m p1_core.cli chat --model qwen3:4b-instruct --message "What do you think about the latest state?"`
   - or `/Users/satojunichi/.openclaw/workspace/systems/p1/bin/p1 chat --model qwen3:4b-instruct --message "What do you think about the latest state?"`

9. Record a world observation
   - `python3 -m p1_core.cli observe --text "A tool run failed during retrieval."`
   - or `/Users/satojunichi/.openclaw/workspace/systems/p1/bin/p1 observe --text "A tool run failed during retrieval."`

10. Queue a bounded world action request
   - `python3 -m p1_core.cli action --kind note --payload "prepare a bounded follow-up action"`
   - or `/Users/satojunichi/.openclaw/workspace/systems/p1/bin/p1 action --kind note --payload "prepare a bounded follow-up action"`

Current limitation:

- P1 now has a direct CLI chat/operator surface through `bin/p1`
- OpenClaw still remains a thin control plane and should not absorb P1 judgment logic

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
- cloud review `approve` / `reject` responses are applied during governance
- approved proposals can mutate versioned policy state under `state/policies/`
- policy snapshots can be rolled back through a current-pointer restore path
- evaluator / governor now read a long-horizon governance profile from `state/governance/`
- short-horizon and long-horizon governance layers are written into daily reports
- a unified operator CLI now exposes ingest / status / approvals / state / rollback from `p1-core`
- an end-to-end lifecycle test now verifies ingest -> approval/policy apply -> operator visibility -> rollback
- the lifecycle acceptance path now covers both policy rollback and proposal rollback through the operator surface
- real Ollama verification has been completed with `qwen3:4b-instruct` through both `p1_core.cli` and the HTTP worker
- low-risk autonomous proposals can now execute a bounded external file action under `state/experiments/actions/`
- prior experiment outcomes now defer reruns until reviewed
- experiment feedback is now accumulated into long-horizon governance and can freeze low-risk autonomy after repeated rerun deferrals
- end-to-end acceptance now covers governance feedback changing a later operator-visible decision
- conversation transcript is persisted under `state/conversation/`
- world observations and queued action requests are persisted under `state/world/`
- `keeper_adapter` reads `glance / daily / approvals` from generated outputs

## 11. Remaining Work

The next meaningful work is no longer basic skeleton building. It is now incremental quality work rather than a missing core subsystem.

## 12. Supporting Documents

These are now supporting docs, not the primary entrypoint:

- [p1-manager-handoff-source-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-manager-handoff-source-2026-04-04.md)
- [p1-canonical-handoff-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-canonical-handoff-2026-04-04.md)
- [p1-external-core-plan-2026-04-04.md](/Users/satojunichi/Documents/openclaw/handoff/p1-external-core-plan-2026-04-04.md)
- [p1-openclaw-bridge-spec-2026-03-30.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-bridge-spec-2026-03-30.md)
- [p1-openclaw-operating-rule-2026-03-29.md](/Users/satojunichi/Documents/openclaw/handoff/p1-openclaw-operating-rule-2026-03-29.md)
- [p1-keeper-handoff-2026-03-29.md](/Users/satojunichi/Documents/openclaw/handoff/p1-keeper-handoff-2026-03-29.md)
- [p1-bootstrap-runbook.md](/Users/satojunichi/Documents/openclaw/p1-core/runbooks/p1-bootstrap-runbook.md)
