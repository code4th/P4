# P4 Runtime Task 1 Evaluation Handoff

Reactivation prompt:

```text
We are continuing from this handoff. Read this document first, inspect the current repo state, verify what still applies, and continue from the next steps without assuming the old chat context is available.
```

## Repo

- Path: `/Users/satojunichi/Documents/openclaw/p4-core`
- Parent git root observed: `/Users/satojunichi/Documents/openclaw`
- Branch observed: `main`
- Main runtime files: `p4_core/runtime.py`, `p4_core/prompts.py`, `p4_core/frames.py`, `p4_core/workspace.py`
- Main tests: `tests/test_runtime.py`, `tests/test_dashboard.py`

## Current Goal

Improve P4 runtime control so P4 can complete Task 1, not solve Task 1 directly in Codex.

Task 1 is a generic Exact Cover solver implementation task:

- Python generic Exact Cover solver
- Algorithm X
- input shape: `row_id -> set(column_id)`
- one-solution and all-solutions APIs
- stats for search nodes/backtracks
- meaningful `unittest`
- external validation by unittest and solution validator

Do not run Task 2, Task 3, Task 4, or Task 5 until Task 1 passes external validation.

## Completed Work

- P4 dashboard was restarted on `127.0.0.1:8899` against `/tmp/p4-eval-exact-cover-56`.
- Latest maze run was analyzed:
  - maze artifact was written
  - `python3 maze_gen.py` ran successfully
  - stdout contained a displayed maze
  - then P4 repeatedly attempted `finish` inside a child frame and runtime blocked it with `child_frame_must_return`
  - final controller completion eventually succeeded from terminal evidence
- Current implementation status was audited for:
  - implementation task progress controller
  - repeated `work_package_invalid` controller
  - operator interrupt logging
  - run56 classification

## Current Findings

### implementation task progress controller

Partially implemented in `p4_core/runtime.py`.

Key functions:

- `_implementation_task_progress_state`
- `_implementation_task_progress_prompt`
- `_implementation_task_phase_action_block`

Implemented/partially implemented phases:

- `implementation_missing`: state and prompt only
- `implementation_present_but_placeholder`: state exists; direct artifact contract blocks placeholders, but phase block is incomplete
- `implementation_present_needs_semantic_review`: implemented
- `tests_missing`: implemented
- `tests_present_needs_semantic_review`: implemented
- `unittest_not_run`: implemented
- `unittest_failed_needs_fix`: state exists but control is incomplete
- `external_contract_satisfied`: state exists but finish-only gating is incomplete

Existing tests include:

- `test_implementation_task_progress_state_requires_tests_after_implementation`
- `test_implementation_task_progress_prompt_rejects_initial_skeletons`
- `test_implementation_missing_after_semantic_review_is_canonical_state`
- `test_implementation_task_progress_allows_fix_when_required_api_is_missing`
- `test_implementation_task_progress_detects_row_id_identity_loss`
- `test_implementation_task_progress_blocks_unittest_when_semantic_review_requires_revision`
- `test_repeated_placeholder_after_semantic_review_stops_run`
- `test_input_contract_violation_after_semantic_review_stops_run`
- `test_repeated_semantic_issue_after_revision_stops_run`

### repeated work_package_invalid controller

Not implemented as a loop controller.

Current implementation has single rejection only:

- `_work_package_blocked_event`
- `_handle_decompose_tasks`
- code: `work_package_invalid`
- reason_code: `missing_work_package_contract`

Missing:

- fingerprint for invalid work packages
- same-invalid comparison using reason, first_action, work_type, and child goal
- temporary ban on repeated `decompose_tasks`
- direct parent action guidance such as `write_file <implementation>.py`
- failure-sample terminal stop after same invalid split repeats

Existing tests cover single rejection:

- `test_open_child_frame_rejects_goal_without_work_package`
- `test_decompose_tasks_rejects_task_without_first_action_contract`
- `test_decompose_tasks_rejects_child_when_why_not_direct_action_says_directly_executable`
- `test_decompose_tasks_rejects_pass_only_python_implementation_first_action`
- dashboard work-package invalid detail rendering around `tests/test_dashboard.py`

### operator interrupt logging

Not implemented.

Current code:

- `worker_loop` installs SIGTERM/SIGINT handler
- handler only sets `stop_requested = True`
- loop exit writes `runtime_status.status = stopped`
- no `interrupted_by_operator` event is appended
- `stop_worker(root)` sends SIGTERM but does not record reason, phase, step, contract, or workspace

Missing fields:

- `reason_code: interrupted_by_operator`
- `operator_reason`
- `stopped_at_step`
- `current_phase`
- `current_tool`
- `current_model`
- `contract_state`
- `missing_requirements`
- `latest_llm_workspace`

### run56 status

`/tmp/p4-eval-exact-cover-56` cannot distinguish P4 failure from external/operator stop in current logs.

Observed:

- Exact Cover operation `da68ead6609044ac8959b93431276569`
- `operation started`
- two `work_package_invalid / missing_work_package_contract` decisions
- no terminal operation event
- final log is an LLM stream fragment with no completed LLM response

Classification possible from logs: incomplete run only.

## Known Problems

- P4 can successfully create and run simple artifacts, but child-frame finalization can loop on blocked `finish`.
- Task 1 Exact Cover still has not passed.
- run56 should not be counted as success or normal P4 failure; it is an incomplete/interrupted sample.
- The current dashboard can show some contract progress, but implementation task progress state is not a full first-class status source.

## Next Steps

1. Add `repeated_work_package_invalid_controller`.
   - Fingerprint invalid split by blocked tool, issues, child goal, work_type, first_action tool/args summary.
   - After repeated invalid decompose, block `decompose_tasks` and require direct action.
   - If it repeats again, emit failure sample and terminal operation status.

2. Add operator interrupt logging.
   - Append session event and canonical event for operator interruption.
   - Record stopped step, phase, current tool/model, contract state, missing requirements, and latest workspace.
   - Ensure operation terminal status is written.

3. Fill implementation progress controller gaps.
   - Block tests/unittest/finish in `implementation_present_but_placeholder`.
   - In `unittest_failed_needs_fix`, allow one useful read then require edit/fix.
   - In `external_contract_satisfied`, allow finish only.

## Tests To Add

- `test_repeated_work_package_invalid_disables_decompose_and_requires_direct_action`
- `test_repeated_work_package_invalid_stops_as_failure_sample`
- `test_work_package_invalid_fingerprint_includes_goal_work_type_first_action_and_issues`
- `test_operator_interrupt_records_structured_event_and_operation_terminal_status`
- `test_operator_interrupt_status_includes_contract_state_and_latest_workspace`
- `test_placeholder_phase_blocks_tests_unittest_and_finish`
- `test_unittest_failed_phase_allows_single_read_then_requires_fix`
- `test_external_contract_satisfied_allows_finish_only`

## Verification Commands Before run57

```bash
python3 -m unittest tests.test_runtime -v
python3 -m unittest tests.test_dashboard -v
```

Focused checks:

```bash
python3 -m unittest \
  tests.test_runtime.RuntimeTests.test_implementation_task_progress_state_requires_tests_after_implementation \
  tests.test_runtime.RuntimeTests.test_implementation_task_progress_blocks_unittest_when_semantic_review_requires_revision \
  tests.test_runtime.RuntimeTests.test_decompose_tasks_rejects_task_without_first_action_contract \
  tests.test_dashboard.DashboardTests -v
```

## Constraints

- Do not directly implement Exact Cover in Codex as the answer.
- P4 must be the actor.
- Success must be external evidence, not P4 self-report.
- Do not run Task 2-5 before Task 1 passes.
- Do not patch observed symptoms without lifting them into runtime contracts.
- For runtime-control code changes, use the L0-L5 abstraction format before editing.
