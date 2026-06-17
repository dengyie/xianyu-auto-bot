# Phase 38 Design - data-card reservation seam in `_auto_delivery(...)`

## Goal

Add focused smoke regressions proving `_auto_delivery(...)` handles data-card reservation outcomes correctly.

## Scope

- Extend the runtime seam smoke suite with deterministic `_auto_delivery(...)` tests.
- Reuse the minimal fake runtime shape from phases 35-37.
- Target the branch where a matched rule has `card_type == "data"`.

## Why This Phase

The previous `_auto_delivery(...)` phases covered basic order shell persistence and bypass behavior.
The next highest-value adjacent contract is data-card reservation because it prevents concurrent orders from receiving the same batch-data line and controls the metadata later used to mark or release the reservation.

## Test Shape

- Success path:
  - Patch `db_manager.reserve_batch_data(...)` to return a reservation payload.
  - Assert the reserved content is returned as delivery content.
  - Assert metadata includes `data_card_pending_consume`, `data_line`, `data_reservation_id`, `data_reservation_status`, and `delivery_unit_index`.
  - Assert reservation receives the selected card id, order id, unit index, cookie id, and buyer id.
- Failure path:
  - Patch `db_manager.reserve_batch_data(...)` to return `None`.
  - Assert `_auto_delivery(..., include_meta=True)` returns failure instead of a prepared delivery payload.
  - Assert no reservation metadata is fabricated.

## Acceptance

- Targeted seam tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-38 diff.
