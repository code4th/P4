# P1 Core Architecture

## Boundary

- `OpenClaw`
  - control plane
  - I/O and tool execution
  - script invocation
- `p1-core`
  - knowledge and policy state
  - critique / proposal / evaluation / governance skeleton
  - local worker orchestration
- `Ollama worker`
  - summarize
  - classify
  - draft lessons

## Initial directory layout

- `p1-core/p1_core/worker`
- `p1-core/p1_core/bootstrap`
- `p1-core/p1_core/reporting`
- `p1-core/p1_core/pipeline`
- `p1-core/p1_core/core`
- `p1-core/p1_core/adapters`
- `p1-core/runtime`
- `p1-core/tests`

## Rollback rule

- append logs, do not overwrite
- scaffold config can be regenerated with `--force`
- promotion remains proposal-only until approval routing exists

## Metaagent-style self-repair

The simplest reliable improvement loop is the one learned from the `metaagent` experiment:

- pick one target file
- show the full current file to the model
- ask for a minimal revision
- validate the candidate
- back up the original before replacement
- restore immediately if validation fails
- record each run in `state/metaagent/generation_history.json`
- surface recent repair runs in the dashboard so self-repair is visible to operators
- allow the model backend to be switched between `ollama` and `openclaw`
- allow the endpoint to be switched with `--base-url` or `--openclaw-config-path` for slower local or gateway-backed verification

This should be treated as a narrow self-repair primitive, not as a replacement for governance, memory, or planning.
