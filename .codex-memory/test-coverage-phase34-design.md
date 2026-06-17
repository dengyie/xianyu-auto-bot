# Phase 34 Design - detail-refresh write-failure isolation

## Goal

Add a failure-path smoke regression proving `XianyuLive.fetch_order_detail_info(...)` does not trigger order-status handler follow-up hooks when persistence reports failure, while still returning the fetched detail payload to the caller.

## Scope

- Extend the runtime seam smoke suite.
- Reuse the real `fetch_order_detail_info(...)` method body with stubbed fetcher and DB dependencies.
- Target the branch where `db_manager.insert_or_update_order(...)` returns `False`.

## Why This Phase

Phase 32 proved the successful persistence path bridges into handler follow-up hooks.
Phase 33 proved handler follow-up exceptions are isolated after successful persistence.

The adjacent consistency contract still uncovered is the opposite side of that boundary: if persistence fails, the runtime must not act as though the order state is durable by invoking handler follow-up logic against data that never committed.

## Test Shape

- Reuse the deterministic detail-refresh seam fixture.
- Patch `fetch_order_detail_simple(...)` to return a successful detail payload.
- Patch the fake DB so:
  - cookie lookup succeeds
  - `insert_or_update_order(...)` returns `False`
- Assert:
  - `fetch_order_detail_info(...)` still returns the fetched detail payload
  - persistence is attempted once
  - `handle_order_detail_fetched_status(...)` is never called
  - `on_order_details_fetched(...)` is never called

## Acceptance

- Targeted runtime seam tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-34 diff.
