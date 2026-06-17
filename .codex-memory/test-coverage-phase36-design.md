# Phase 36 Design - basic-order-info write-failure isolation

## Goal

Add a focused smoke regression proving `_auto_delivery(...)` still returns prepared delivery content when the initial basic-order-info persistence returns `False`, while skipping the basic-order-info status handler.

## Scope

- Extend the runtime seam smoke suite with one deterministic `_auto_delivery(...)` test.
- Reuse the minimal fake runtime shape from phase 35.
- Target the branch where:
  - `db_manager.insert_or_update_order(...)` returns `False`
  - `handle_order_basic_info_status(...)` must not run
  - delivery content preparation should still succeed

## Why This Phase

Phase 35 proved `_auto_delivery(...)` isolates post-write handler failures.
The adjacent symmetry gap is the persistence-failure branch itself.

This matters because the method is currently designed to keep preparing delivery content even if the prewrite fails. That behavior should stay explicit and protected: callers still receive the prepared content, but no downstream status helper should run against an order shell that did not persist.

## Test Shape

- Construct `XianyuLive` via `__new__` with only `_auto_delivery(...)` dependencies.
- Patch DB methods to return:
  - one matching text rule
  - valid cookie lookup
  - no existing order
  - `insert_or_update_order(...) == False`
- Stub detail fetch and delivery step construction.
- Assert:
  - `_auto_delivery(..., include_meta=True)` still returns a successful text delivery result
  - `insert_or_update_order(...)` is attempted once
  - `handle_order_basic_info_status(...)` is never called

## Acceptance

- Targeted seam tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-36 diff.
