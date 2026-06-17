# Phase 37 Design - existing-order bypass for basic-order-info prewrite

## Goal

Add a focused smoke regression proving `_auto_delivery(...)` skips basic-order-info prewrite and handler follow-up when the order already exists, while still returning prepared delivery content.

## Scope

- Extend the runtime seam smoke suite with one deterministic `_auto_delivery(...)` test.
- Reuse the minimal fake runtime shape from phases 35-36.
- Target the branch where:
  - `db_manager.get_order_by_id(...)` returns an existing order
  - `db_manager.insert_or_update_order(...)` should not be called
  - `handle_order_basic_info_status(...)` should not be called

## Why This Phase

The recent `_auto_delivery(...)` coverage locked down the new-order write/failure boundaries.
The next adjacent contract is the bypass branch for already persisted orders, which prevents duplicate writes and duplicate status helper side effects.

This is distinct from the write-failure branch because the code path should never even try to prewrite again once an order shell already exists.

## Test Shape

- Construct `XianyuLive` via `__new__` with only `_auto_delivery(...)` dependencies.
- Patch DB methods to return:
  - one matching text rule
  - valid cookie lookup
  - an existing order object
- Ensure `insert_or_update_order(...)` is not called.
- Ensure `handle_order_basic_info_status(...)` is not called.
- Assert `_auto_delivery(..., include_meta=True)` still returns a successful delivery result.

## Acceptance

- Targeted seam tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-37 diff.
