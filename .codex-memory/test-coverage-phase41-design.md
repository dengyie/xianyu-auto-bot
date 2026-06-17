# Phase 41 Design - pending-finalize replay on manual delivery retry

## Goal

Add focused route-level smoke coverage for the manual-delivery retry path that replays previously sent but unfinalized delivery units.

## Scope

- Extend the manual delivery transition smoke suite.
- Reuse the fake runtime harness from phases 39-40.
- Target the route branch that:
  - detects `pending_finalize_unit_indexes` before preparing new delivery units
  - loads persisted delivery metadata via `_get_pending_delivery_finalization_meta(...)`
  - retries `finalize_delivery_after_send(...)` without re-sending the buyer message

## Why This Phase

Phase 40 proved the system preserves a recoverable `partial_pending_finalize` state after send-side finalization failure.
The next adjacent contract is the recovery path itself: a later manual retry must consume that saved state, finish the post-send side effects, and avoid duplicating message delivery.

## Test Shape

- Success replay path:
  - seed a `sent` delivery finalization state in the DB for one unit
  - have the fake runtime return that saved metadata from `_get_pending_delivery_finalization_meta(...)`
  - keep `_finalize_delivery_after_send(...)` successful
  - assert the route reports delivered success without re-sending message content
  - assert progress becomes `shipped`
- Failed replay path:
  - seed the same `sent` state
  - make `_finalize_delivery_after_send(...)` return `{"success": False, "error": ...}`
  - assert the route returns `success=False` and `delivered=False`
  - assert the unit remains pending-finalize in progress summary

## Acceptance

- Targeted transition tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-41 diff.
