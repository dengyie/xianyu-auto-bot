# Phase 40 Design - reservation-backed finalize-after-send failure at manual delivery route

## Goal

Add focused smoke coverage proving manual delivery keeps reservation-backed units in a pending-finalize state when `finalize_delivery_after_send(...)` fails after a successful send.

## Scope

- Extend the manual delivery transition smoke suite.
- Reuse the fake runtime harness introduced in phase 39.
- Target the route-level branch where:
  - message sending succeeds
  - reservation mark-sent succeeds
  - `finalize_delivery_after_send(...)` returns a failure payload

## Why This Phase

Phase 39 proved reservation-backed units close their reservation correctly on the send path.
The next adjacent production risk is the post-send finalization seam: once the buyer has already received the content, the system must preserve that sent state for later completion instead of reporting the unit as fully finalized or dropping it back to pending ship.

## Test Shape

- Prepare a fake runtime whose `_auto_delivery(...)` returns `data` card metadata and whose `_finalize_delivery_after_send(...)` returns `{"success": False, "error": ...}`.
- Execute `/api/orders/{order_id}/deliver`.
- Assert:
  - the route returns success but not delivered
  - the reservation is marked sent and not released
  - delivery progress becomes `partial_pending_finalize`
  - the pending-finalize count is `1`
  - a failed delivery log is recorded for the finalize failure

## Acceptance

- Targeted transition tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-40 diff.
