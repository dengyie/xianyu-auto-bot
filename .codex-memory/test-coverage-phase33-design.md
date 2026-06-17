# Phase 33 Design - detail-refresh handler failure isolation

## Goal

Add a failure-path smoke regression proving `XianyuLive.fetch_order_detail_info(...)` preserves a successful detail refresh result even when the order-status handler follow-up logic raises.

## Scope

- Extend the runtime seam smoke suite.
- Reuse the real `fetch_order_detail_info(...)` method body with stubbed fetcher and DB dependencies.
- Target the exception-isolation behavior around:
  - `handle_order_detail_fetched_status(...)`
  - `on_order_details_fetched(...)`

## Why This Phase

Phase 32 already proves the happy path bridges successful detail persistence into handler follow-up hooks.

The next high-value gap is the adjacent robustness contract: callers such as delayed refreshes, forced refreshes, and route-level refresh entrypoints should still receive the fetched detail payload even if the handler's queue-consumption follow-up fails after persistence.

## Test Shape

- Reuse the deterministic detail-refresh seam fixture from phase 32.
- Patch `fetch_order_detail_simple(...)` to return a successful detail payload.
- Patch the fake DB so order persistence succeeds.
- Configure `order_status_handler.on_order_details_fetched(...)` to raise.
- Assert:
  - `fetch_order_detail_info(...)` still returns the fetched detail payload
  - persistence still occurs
  - `handle_order_detail_fetched_status(...)` is called before the failure
  - `on_order_details_fetched(...)` is attempted once
  - the exception does not escape to the caller

## Acceptance

- Targeted runtime seam tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-33 diff.
