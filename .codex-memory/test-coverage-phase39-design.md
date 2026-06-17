# Phase 39 Design - data-card reservation mark/release after manual delivery send

## Goal

Add focused smoke regressions proving manual delivery closes the data-card reservation loop after a send attempt.

## Scope

- Extend the manual delivery transition smoke suite.
- Reuse the existing fake runtime harness in `tests/smoke/test_order_delivery_transitions.py`.
- Target the route-level contract that consumes `_auto_delivery(...)` reservation metadata and then:
  - marks the reservation as sent after a successful message send
  - releases the reservation when the post-send mark step fails

## Why This Phase

Phase 38 locked down reservation creation inside `_auto_delivery(...)`.
The next adjacent production risk is the send-side cleanup path: if a reservation is never marked sent or released after a failed post-send transition, batch data can become stranded or be delivered inconsistently on retries.

## Test Shape

- Success path:
  - prepare a fake runtime whose `_auto_delivery(...)` returns a `data` card payload with reservation metadata
  - execute `/api/orders/{order_id}/deliver`
  - assert the runtime records one mark-sent call and zero release calls
  - assert the delivery still finalizes successfully
- Mark-failure path:
  - return the same reservation metadata
  - force `_mark_data_reservation_sent_if_needed(...)` to return `False`
  - assert the runtime records a release call with a post-send failure reason
  - assert the order is not reported as delivered/finalized

## Acceptance

- Targeted transition tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-39 diff.
