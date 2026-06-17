# Phase 42 Design - pending-finalize-only completion return on manual delivery retry

## Goal

Add focused route-level smoke coverage for the manual delivery branch that finishes existing pending-finalize units and exits without preparing any new delivery units.

## Scope

- Extend the manual delivery transition smoke suite.
- Reuse the fake runtime harness from phase 41.
- Target the branch where:
  - `pending_finalize_unit_indexes` exists before any new delivery preparation
  - replay finalization succeeds
  - `remaining_unit_indexes` becomes empty

## Why This Phase

Phase 41 proved pending-finalize replay can succeed and fail.
The adjacent contract is the “replay-only” fast exit: when the order no longer has any unsent units, manual delivery should return success immediately after completing the replay instead of continuing through the normal send-preparation path.

## Test Shape

- Seed a `sent` delivery finalization state for one unit.
- Configure the fake runtime to replay that unit successfully through `_get_pending_delivery_finalization_meta(...)`.
- Assert:
  - the route returns `success=True` and `delivered=True`
  - no send methods are called
  - no new `_auto_delivery(...)` preparation is attempted
  - progress ends at `shipped`
  - the response message indicates the run only completed prior pending-finalize work

## Acceptance

- Targeted transition tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-42 diff.
