# P4 Execution Route Contract

Date: 2026-04-26

This document fixes the route-level contract for P4 output handling. The goal is
not to repair Markdown-wrapped JSON after the fact. The goal is to route each
interaction through the owner that can produce and validate the correct kind of
output.

## Execution Route Table

| Route | Input | Output | LLM may decide | Runtime must decide | Schema required | Streaming | Failure fallback | Logged observations |
|---|---|---|---|---|---|---|---|---|
| Normal user response | User chat that does not require tools or runtime control | Natural language assistant message | Wording and general answer content | Session state, stream capture, final persistence | No | Yes | Record LLM error; return failed chat event | role, model, content_text, thinking_text, stream timing |
| Runtime profile/status fact | Identity/status/profile queries such as "お名前は？" | Deterministic answer from runtime profile | Nothing | The canonical runtime fact and answer | No | No | No LLM fallback; runtime profile is the source of truth | route, evidence_type=runtime_profile, answer, profile source |
| Agent machine-control | User task requiring tools, frames, or finish state | Exactly one machine-control JSON object | Choose one allowed tool/action and arguments within schema | Transport, schema, parser acceptance, tool execution, state transition | Yes | No | Bounded retry; on parse/schema failure fail the operation without executing the envelope | transport, raw_output_is_machine_json, schema_validation_ok, parse_issue, schema errors, raw preview |
| Grounding/evidence judge | Final answer plus evidence package | Exactly one judge JSON object | Verdict within schema | Evidence package construction and accept/block decision | Yes | No | Bounded retry; invalid verdict is judge unavailable or blocked according to caller policy | evidence classes, verdict, raw_output_is_machine_json, schema_validation_ok, unsupported claims |

## Failure Taxonomy

| Failure | Meaning | Owner | Correct handling |
|---|---|---|---|
| `empty_output` | Visible content is empty | LLM route | Retry if allowed; otherwise fail with observation |
| `thinking_only_output` | Useful text is hidden in thinking, not visible content | LLM route | One visible-content repair if allowed; never treat hidden thinking as action |
| `missing_json_object` | No JSON object exists in visible content | LLM route | Fail or bounded repair |
| `json_parse_error` | JSON-like text exists but cannot parse | LLM route | Fail or bounded repair |
| `json_extraneous_text` | A JSON object exists but the raw output is not exactly that object | Producer contract | Do not strip wrapper; mark `raw_output_is_machine_json=false` |
| `schema_validation_failed` | Raw JSON object exists but does not match schema | Producer contract | Do not execute; record schema errors |
| `length_truncated` | Generation stopped before a complete object | Transport/model budget | Retry with chunking instruction if allowed |
| `grounding_issues` | Final answer is not supported by evidence package | Runtime decision | Block finish or use route-specific deterministic evidence |

## Minimal Patch Plan

1. Machine-control generation uses nonstream `chat` with structured schema.
2. Runtime status records `raw_output_is_machine_json` and `schema_validation_ok`
   separately.
3. Runtime identity queries bypass agent loop and grounding judge, using
   `p4_core.runtime_profile` as the source of truth.
4. Schema parse failure no longer retries through the same streaming
   machine-control path because that path is not used for machine-control.
5. Grounding evidence is packaged into `runtime_facts`, `tool_facts`, and
   `external_facts` so the judge cannot conflate runtime identity with file or
   environment evidence.

## Verification Cases

| Case | Expected route | Expected result |
|---|---|---|
| `お名前は？` | Runtime profile/status fact | LLM is not called; answer comes from runtime profile |
| Markdown-wrapped machine JSON | Agent machine-control | Rejected with `json_extraneous_text`; `raw_output_is_machine_json=false`; `schema_validation_ok=true` |
| Extra top-level JSON field | Agent machine-control | Rejected with `schema_validation_failed`; `raw_output_is_machine_json=true`; `schema_validation_ok=false` |
| Valid tool JSON | Agent machine-control | Accepted; nonstream response event; no stream chunks |
| Judge verdict with runtime identity evidence | Grounding/evidence judge | Runtime facts are explicit in evidence package |

## Runtime Contract Design Discipline

P4 is not responsible for making runtime-control decisions correct. The runtime
that drives P4 is responsible for defining and enforcing the control contract.
Observed failures must be treated as contract signals, not as isolated bugs.

Before implementing any runtime-control fix, the work must declare:

1. 観測事実 L0
2. 直接原因 L1
3. 同型失敗 L2
4. 破れているruntime契約 L3
5. 責務分離 L4
6. 最小修正 L5
7. 再発防止テスト

No implementation should start from L0-L1 alone. The analysis must reach at
least L3 so the fix is tied to a runtime contract or invariant.

Required artifact shape:

- A. 問題の抽象化
- B. runtime不変条件
- C. 実装差分
- D. 再発防止テスト

Prohibited shortcuts:

- Do not fix only the observed error string.
- Do not treat regex or post-hoc repair of LLM output as the solution.
- Do not conclude that the runtime is safe because a judge blocked one case.
- Do not end with "the model is bad."
- Do not end with "make the prompt stronger."
- Do not collapse JSON failures, finish failures, and grounding failures into a
  single failure type.
- Do not accept one success case as completion.
- Do not assign the failure to P4 itself; assign it to the runtime design that
  controls P4.
