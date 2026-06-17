# Phase 15 Test Coverage Design

## Scope

Broaden pending-update entrypoint coverage around `on_order_details_fetched(...)` so the real detail-fetched path proves it can consume multiple queued status updates for the same order in sequence.

## Target behavior

1. `on_order_details_fetched(order_id)`
   - when one order has multiple queued pending updates
   - the handler should consume all of them in order
   - the final order status should reflect the last applied update
   - the pending queue entry for that order should be removed

## Test strategy

- Extend `tests/smoke/test_order_status_pending_updates.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one order with two queued pending updates that represent a realistic progression.
- Trigger `on_order_details_fetched(order_id)` directly.
- Assert:
  - pending updates are fully consumed
  - final order status matches the last queued update

## Planned cases

1. detail-fetched entrypoint consumes multiple queued updates for one order and applies them in sequence

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_pending_updates.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
