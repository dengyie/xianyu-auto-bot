# Phase 32 Design - fetch_order_detail_info Handler Seam Coverage

## Goal

Add a higher-level smoke regression proving `XianyuLive.fetch_order_detail_info(...)` triggers the order-status handler follow-up hooks after a successful detail refresh persists the order.

## Scope

- Add one focused smoke test around the real `fetch_order_detail_info(...)` method body.
- Stub external detail fetching and DB dependencies.
- Verify the successful save path calls:
  1. `handle_order_detail_fetched_status(...)`
  2. `on_order_details_fetched(...)`

## Why This Phase

Current coverage already proves:

- handler-level pending queue behavior
- runtime message handoff seams in `handle_message(...)`
- route-level refresh/history sync happy paths

What is still missing is direct proof that the service/runtime detail-refresh entrypoint bridges a successful detail fetch into the handler queue-consumption hooks.

## Test Shape

- Construct `XianyuLive` via `__new__` with only the attributes used by `fetch_order_detail_info(...)`.
- Patch `utils.order_detail_fetcher.fetch_order_detail_simple(...)` to return a deterministic detail payload.
- Patch `db_manager.db_manager` methods with a narrow fake/mock surface.
- Assert:
  - the method returns the fetched detail payload
  - order persistence is attempted with the expected identity/context fields
  - `handle_order_detail_fetched_status(...)` is called with the refreshed order id and cookie id
  - `on_order_details_fetched(...)` is called for the same order after the status hook

## Acceptance

- Targeted seam test passes.
- Full smoke suite passes.
- `compileall` passes for the touched Python modules.
- Production review reports no new blocking findings for the phase-32 diff.
