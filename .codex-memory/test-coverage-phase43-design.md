# Phase 43 Design - refresh route failure response when live detail refresh returns no result

## Goal

Add focused route-level smoke coverage for the order refresh endpoint when the live detail refresh returns no usable result.

## Scope

- Extend `tests/smoke/test_order_delivery_transitions.py`.
- Reuse the existing fake cookie manager and runtime harness.
- Target `/api/orders/{order_id}/refresh`.

## Why This Phase

The refresh route already has smoke coverage for the ownership rejection branch and the successful status update branch.
The adjacent production contract is the soft-failure path where `fetch_order_detail_info(...)` does not raise, but also does not return a truthy detail result.

That route must report `success=False` and `updated=False` instead of silently claiming the refresh worked.

## Test Shape

- Seed a refreshable order owned by the current user.
- Configure the fake runtime so `fetch_order_detail_info(...)` returns `None`.
- Assert:
  - the route returns HTTP 200 with `success=False` and `updated=False`
  - the stored order status is unchanged
  - the response message clearly reports refresh failure

## Acceptance

- Targeted transition tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-43 diff.
