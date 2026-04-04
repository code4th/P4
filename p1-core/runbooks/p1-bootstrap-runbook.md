# P1 Bootstrap Runbook

## Goal

Create a reproducible P1 workspace without relying on internal OpenClaw agent creation.

## Steps

1. Run the bootstrap scaffolder.
   - `cd /Users/satojunichi/Documents/openclaw/p1-core`
   - `python3 -m p1_core.bootstrap.bootstrap_p1 --root /Users/satojunichi/.openclaw/workspace/systems/p1`
2. Start the local worker.
   - `python3 -m p1_core.worker.ollama_worker --port 8765`
3. Verify health.
   - `curl http://127.0.0.1:8765/health`
4. Generate initial P1 reports.
   - `python3 -m p1_core.reporting.write_example_reports --root /Users/satojunichi/.openclaw/workspace/systems/p1`
5. Run the minimal growth loop on a real text input when ready.
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --input-text "example observation"`
6. Use OpenClaw only through a thin adapter.
   - read files in `state/reports/`
   - invoke `intervene.js`

## Verified outputs after growth loop

- `state/knowledge/knowledge.jsonl`
- `state/events/event-log.jsonl`
- `state/proposals/latest-proposals.json`
- `state/proposals/snapshots/*.json`
- `state/reports/daily/*-glance.json`
- `state/reports/daily/*-daily.json`
- `state/health.json`

## Verified outputs after governance update

- knowledge records can be transitioned to `deferred`, `active`, or `retired`
- event log records the transition reason and actor
- proposal snapshots can be compared with the latest snapshot before promotion or rollback
- governance review is written into proposal snapshots and daily reports
- rollback updates `glance`, `daily`, and `health` so bridge-visible state stays aligned
- evaluator considers previous snapshot duplication and state history for `active / deferred / retired`
- approval-gated proposals emit cloud-review request files under `state/cloud_evaluation/requests/`
- cloud review responses in `state/cloud_evaluation/responses/` are applied during ingest
- policy snapshots are written under `state/policies/snapshots/` when proposals are approved
- policy state can be restored with `--rollback-policy-snapshot-id`
- governance profile is read from `state/governance/latest-governance.json`
- daily reports include short-horizon and long-horizon governance sections

## Rollback

1. Stop the worker process.
2. Restore a previous proposal snapshot.
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --rollback-snapshot-id 2026-04-04-proposals`
3. Restore a previous policy snapshot when needed.
   - `python3 -m p1_core.pipeline.growth_loop --root /Users/satojunichi/.openclaw/workspace/systems/p1 --rollback-policy-snapshot-id baseline-policy`
4. Confirm `state/proposals/latest-proposals.json` or `state/policies/latest-policy.json` now points to the restored snapshot.
5. Confirm OpenClaw bridge now reads `rollback_applied` or `policy_rollback_applied` from `status` and `report`.
6. Move failed artifacts into `state/archive/` only after the restored state is confirmed.
7. Do not mutate OpenClaw-side policy logic during rollback.
