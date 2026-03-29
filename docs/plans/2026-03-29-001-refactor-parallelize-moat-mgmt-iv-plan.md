---
title: "refactor: Parallelize management + intrinsic value after moat gate"
type: refactor
status: completed
date: 2026-03-29
origin: docs/ideation/2026-03-27-open-ideation.md (idea #6)
deepened: 2026-03-29
---

# refactor: Parallelize Management + Intrinsic Value After Moat Gate

## Overview

Restructure the Step Functions state machine so management quality and intrinsic value run in parallel after the moat gate passes. This cuts wall-clock pipeline time by ~30-40% per ticker with minimal architectural change.

## Problem Frame

The analysis pipeline runs five stages sequentially: quant -> moat -> management -> intrinsic value -> thesis. Moat and management are both Sonnet-tier LLM calls (~30-60s each). Intrinsic value is pure math (~2-5s). Management and IV have zero data dependency on each other — both need only the quant screen output plus moat results. Running them sequentially wastes ~30-60s per ticker.

## Requirements Trace

- R1. Management and intrinsic value run concurrently after the moat gate
- R2. Moat gate (>= 7) preserved — management and IV only run for moat-passing tickers
- R3. Management prompt retains moat_score context
- R4. Thesis generator receives the same accumulated state as before (all fields from moat, management, IV)
- R5. Error in one parallel branch does not silently swallow the other branch's result
- R6. No change to Lambda handler input/output contracts (except the new merge handler)

## Scope Boundaries

- No changes to any analysis Lambda handler logic (moat, management, IV, thesis)
- No changes to the management prompt template
- No changes to the quant screen or thesis generator
- No changes to DynamoDB storage patterns
- The only new code is a small merge Lambda and CDK rewiring

## Context & Research

### Relevant Code and Patterns

- `infra/stacks/analysis_stack.py` — CDK state machine definition, `_fn()` Lambda factory, retry/catch patterns
- `src/analysis/management_quality/handler.py` — reads `moat_score` from event at line 88, uses it in prompt template
- `src/analysis/intrinsic_value/handler.py` — reads only `ticker` and `metrics` from event; does NOT use `moat_score` or `management_score` computationally
- `src/analysis/thesis_generator/handler.py` — `_passes_all_stages()` checks `moat_score >= 7`, `management_score >= 6`, `margin_of_safety > mos_threshold`; reads all fields from flat event dict
- All handlers follow the pass-through pattern: `result = dict(event)` then overlay their output fields
- `sfn.Map` with `max_concurrency=3` is the only existing parallelism — no `sfn.Parallel` in the codebase yet

### Data Flow Pattern

Each handler copies its entire input via `dict(event)` and adds its own fields. With `output_path="$.Payload"`, the Step Functions state accumulates all fields. After parallelization, `sfn.Parallel` will output an array of two dicts (one per branch). A merge step must flatten this back into a single dict before thesis can consume it.

## Key Technical Decisions

- **Keep moat first with gate**: Preserves the moat_score context in management's prompt and avoids wasting Sonnet calls on low-moat tickers. The ~30% time savings (vs ~40% with full parallelization) is the right tradeoff for correctness and cost.

- **Merge via Lambda, not Pass state**: `sfn.Pass` with `ResultSelector` cannot do deep dict merges. A small Lambda (~15 lines) that flattens the Parallel output array into one dict is simpler, testable, and future-proof.

- **Parallel branch error handling**: If one branch fails and the other succeeds, `sfn.Parallel` fails the entire parallel block. The Map-level catch handler already handles this by routing to `AnalysisPipelineFailed`. This matches existing behavior where any stage failure fails the ticker's pipeline run.

## Open Questions

### Resolved During Planning

- **Q: Should IV also run in parallel with management?** Yes. IV reads only `ticker` and `metrics` from the quant screen output — it does not use `moat_score` or `management_score` computationally. Running it in parallel with management is safe and free.
- **Q: How does the merge work?** `sfn.Parallel` outputs `[branch_0_result, branch_1_result]`. Both branches have all input fields (via `dict(event)` pass-through) plus their own additions. The merge Lambda iterates the array and merges all dicts, with later entries overwriting earlier ones. Since management and IV add disjoint keys, order doesn't matter.
- **Q: Does the MoS gate still work?** Yes. The merge Lambda produces a single flat dict with `margin_of_safety` (from IV) at the top level, exactly as before.

### Deferred to Implementation

- None — all planning questions resolved.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
Current pipeline (per ticker inside Map):

  moat -> [moat >= 7?] -> mgmt -> iv -> [MoS > 0.30?] -> thesis
                |
           MoatFailed                                  InsufficientMoS


New pipeline (per ticker inside Map):

  moat -> [moat >= 7?] -> Parallel ─┬─ mgmt ─┐
                |                    └─ iv   ─┘
           MoatFailed                    |
                                      merge
                                        |
                                  [MoS > 0.30?] -> thesis
                                        |
                                  InsufficientMoS
```

The merge Lambda receives:
```json
[
  {"ticker": "AAPL", "moat_score": 8, ..., "management_score": 7, ...},
  {"ticker": "AAPL", "moat_score": 8, ..., "margin_of_safety": 0.45, ...}
]
```

And produces:
```json
{"ticker": "AAPL", "moat_score": 8, "management_score": 7, "margin_of_safety": 0.45, ...}
```

## Implementation Units

- [x] **Unit 1: Create merge results Lambda handler**

  **Goal:** A minimal Lambda that flattens the `sfn.Parallel` output array into a single dict.

  **Requirements:** R4, R6

  **Dependencies:** None

  **Files:**
  - Create: `src/analysis/merge_results/__init__.py`
  - Create: `src/analysis/merge_results/handler.py`
  - Test: `tests/unit/analysis/test_merge_results.py`

  **Approach:**
  - The handler receives `event` as a list of dicts (the Parallel output)
  - Iterate and merge all dicts into one using `dict.update()` (or `{**a, **b}` unpacking)
  - Return the merged dict
  - No DynamoDB, no LLM, no external calls — pure dict manipulation
  - Log a warning if the event is not a list (defensive, in case of misconfiguration)

  **Patterns to follow:**
  - Keep it minimal — this is infrastructure glue, not business logic
  - Follow the same handler signature as other analysis handlers: `handler(event, context) -> dict`

  **Test scenarios:**
  - Two-element list with disjoint keys -> merged dict with all keys
  - Two-element list with overlapping pass-through keys (`ticker`, `company_name`, `metrics`, `moat_score`) -> values identical, no data loss
  - Empty list -> empty dict
  - Single-element list -> returns that element unchanged
  - Non-list input -> logs warning, returns event as-is (defensive)

  **Verification:**
  - Merged output contains all keys from both management and IV handler outputs
  - No data loss from either branch
  - Key contract: management branch produces `management_score`, `owner_operator_mindset`, `capital_allocation_skill`, `candor_transparency`, `red_flags`, `green_flags`. IV branch produces `intrinsic_value_per_share`, `margin_of_safety`, `buy_signal`, `scenarios`, `dcf_per_share`, `epv_per_share`, `floor_per_share`, `mos_threshold`. Both branches pass through `ticker`, `company_name`, `metrics`, `moat_score` from moat output

- [x] **Unit 2: Rewire CDK state machine to use sfn.Parallel**

  **Goal:** Restructure the Step Functions definition so management and IV run in parallel branches after the moat gate.

  **Requirements:** R1, R2, R3, R5

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `infra/stacks/analysis_stack.py`

  **Approach:**
  - Add the merge Lambda via `_fn("MergeResults", "analysis.merge_results.handler.handler", ...)`. The `_fn()` factory unconditionally attaches the shared deps layer and grants DynamoDB/S3/SSM IAM — this is fine even though the merge Lambda needs none of it; no runtime cost for unused permissions
  - **Rewrite the chain construction block (lines 317-329), not just line 323.** In CDK, once a state is connected via `.next()`, it cannot be reattached to a different construct. The existing code chains `mgmt_step.next(iv_step).next(...)` — these steps become linked and cannot be independently passed to `sfn.Parallel.branch()`. The fix: remove the existing `.next()` chain entirely and construct the Parallel block before any chaining. `mgmt_step` and `iv_step` must remain standalone `LambdaInvoke` objects when passed to `.branch()`
  - Create an `sfn.Parallel` construct with two branches. `sfn.Parallel` passes the **same input** to all branches — both mgmt and IV receive the full moat output dict, which is exactly what both handlers expect
    - Branch 0: `mgmt_step` (standalone, not chained)
    - Branch 1: `iv_step` (standalone, not chained)
  - Add a `merge_step` (`sfn_tasks.LambdaInvoke` with `output_path="$.Payload"`) after the Parallel. The Parallel itself has no `output_path` — it outputs the raw array `[branch_0_result, branch_1_result]`, which the merge Lambda flattens
  - Build the new chain: `moat_pass.when(..., parallel_block.next(merge_step).next(mos_pass.when(...)))` — replacing the entire block at lines 317-329
  - Apply the shared `_retry_kwargs` to the merge step
  - Update the module docstring and Map comment to reflect the new flow
  - With `max_concurrency=3` on the Map and 2 Parallel branches, up to 6 concurrent Lambda invocations are possible (3 tickers x 2 branches). This is within normal Lambda concurrency limits

  **Patterns to follow:**
  - `infra/stacks/analysis_stack.py` — `_fn()` factory (lines 156-185), `LambdaInvoke` construction, retry config, chain construction via `.next()` and `Choice.when()`
  - CDK `sfn.Parallel` API: `.branch(chain1).branch(chain2)`
  - All existing `LambdaInvoke` tasks use `output_path="$.Payload"` to strip the Lambda response wrapper

  **Test scenarios:**
  - `cdk synth` succeeds without errors
  - Generated state machine JSON shows Parallel state with two branches
  - Moat gate still routes low-moat tickers to MoatFailed Pass state
  - MoS gate still routes low-MoS tickers to InsufficientMoS Pass state
  - Merge step is present between Parallel and MoS check

  **Verification:**
  - `cdk synth` produces valid CloudFormation
  - State machine definition reflects the new parallel structure
  - No changes to any existing Lambda handler code

- [x] **Unit 3: Data flow tests for parallel pipeline**

  **Goal:** Verify the end-to-end data flow works correctly with the parallel structure by invoking handlers directly in Python (not Step Functions execution tests).

  **Requirements:** R1, R4, R5

  **Dependencies:** Unit 1, Unit 2

  **Files:**
  - Modify: `tests/integration/test_analysis_pipeline.py` (add handler-invocation data flow tests)
  - Test: `tests/unit/analysis/test_merge_results.py` (already created in Unit 1)

  **Approach:**
  - Note: `test_analysis_pipeline.py` currently only tests quant_screen via direct Python handler calls — no Step Functions execution tests exist. Follow the same pattern: invoke handlers directly, passing outputs between them in Python to simulate the pipeline flow
  - Simulate the parallel data flow: call moat handler, then call mgmt and IV handlers with moat output independently, then pass both outputs as a list to the merge handler, then verify the merged dict has everything thesis needs
  - CDK structural correctness is verified separately via `cdk synth` in Unit 2

  **Patterns to follow:**
  - `tests/integration/test_analysis_pipeline.py` — direct handler invocation pattern with moto mocks
  - `tests/unit/` — moto-based unit test patterns

  **Test scenarios:**
  - Happy path: moat output -> call mgmt handler and IV handler independently -> merge both outputs -> verify merged dict contains all fields thesis needs
  - Merge handler receives realistic handler outputs -> assert these specific keys are present: `moat_score`, `moat_type`, `moat_sources`, `management_score`, `owner_operator_mindset`, `capital_allocation_skill`, `candor_transparency`, `intrinsic_value_per_share`, `margin_of_safety`, `buy_signal`, `scenarios`, `dcf_per_share`, `epv_per_share`, `floor_per_share`, `mos_threshold`, `ticker`, `company_name`, `metrics`, `quant_result`
  - Management and IV outputs have overlapping pass-through keys (`ticker`, `company_name`, `metrics`, `moat_score`) with identical values -> merge produces correct result
  - One handler output missing (simulating branch failure) -> merge of single-element list returns that element

  **Verification:**
  - All tests pass
  - Thesis generator's required fields are explicitly enumerated and asserted in at least one test case

## System-Wide Impact

- **Interaction graph:** Only the Step Functions state machine definition changes. All Lambda handlers remain unchanged. The merge Lambda is new but stateless — no DynamoDB, S3, or external calls.
- **Error propagation:** `sfn.Parallel` fails if any branch fails, but Step Functions **does not cancel the surviving branch** — it runs to completion before the Parallel state reports failure. This means a failed parallel execution leaves partial analysis results in DynamoDB (e.g., management wrote its record but IV failed, or vice versa). This is benign — it matches the current sequential behavior (if IV fails, moat and management records already exist) and each handler writes to a distinct sort key. But it is worth knowing during incident debugging: the `analysis` table may contain records from a "failed" pipeline run.
- **Concurrent DynamoDB writes:** Both management and IV call `store_analysis_result()` in parallel, writing to the same partition key (ticker) but different sort keys (`{date}#management_quality` vs `{date}#intrinsic_value`). This is fully safe — DynamoDB on-demand handles concurrent writes to different items without contention. No conditional writes or read-modify-write patterns are involved.
- **State lifecycle risks:** None. The merge is a pure function over immutable input. No writes, no side effects.
- **API surface parity:** Dashboard and other consumers read from the `analysis` DynamoDB table, which is written by each handler independently. The parallel change does not affect DynamoDB writes.
- **Payload size:** Per-ticker payloads stay well under 10KB even after the Parallel doubling (~3KB moat output x 2 branches = ~7.5KB array, merged back to ~4.5KB). The Step Functions 256KB payload limit is not a concern.
- **Cost impact:** One additional Lambda invocation per ticker (the merge handler) — negligible cost (~$0.000001 per invocation). No additional LLM calls.

## Risks & Dependencies

- **CDK Parallel construct behavior**: `sfn.Parallel` outputs an array, not a merged dict. The merge Lambda is essential — without it, the thesis generator would receive an array instead of a dict and fail. This is well-documented CDK/Step Functions behavior.
- **Surviving branch on failure**: When one Parallel branch fails, the other runs to completion (including its DynamoDB write and any LLM cost). This is not a regression from sequential behavior but is worth noting: a management LLM call (~$0.01-0.03) cannot be cancelled if IV fails first.
- **Deployment**: The state machine update is a CloudFormation update. In-flight executions (if any) will continue with the old definition. New executions use the new definition. No migration needed.
- **Rollback**: If issues arise, reverting the CDK change restores sequential execution. The merge Lambda is harmless to leave deployed.

## Sources & References

- **Origin document:** [docs/ideation/2026-03-27-open-ideation.md](docs/ideation/2026-03-27-open-ideation.md) (idea #6: Pipeline DAG)
- Related code: `infra/stacks/analysis_stack.py` (state machine), `src/analysis/*/handler.py` (all five handlers)
- AWS docs: Step Functions Parallel state type
