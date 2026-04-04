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
