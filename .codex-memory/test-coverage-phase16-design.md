# Phase 16 Test Coverage Design

## Scope

Broaden pending-update batch processing coverage around `process_all_pending_updates(...)` so the real queue drain path proves it can consume multiple order buckets in one pass.

## Target behavior

1. `process_all_pending_updates()`
   - when multiple order IDs have queued updates
   - the handler should process each order bucket
   - each order should land in the final queued state for that order
   - `pending_updates` should be empty afterward

## Test strategy

- Extend `tests/smoke/test_order_status_pending_updates.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed two different orders with queued updates.
- Trigger `process_all_pending_updates()` directly.
- Assert:
  - both orders are processed
  - the return value matches the number of processed orders
  - the queue is empty afterward

## Planned cases

1. batch pending-update processing drains two order buckets and applies both sets of updates

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_pending_updates.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
