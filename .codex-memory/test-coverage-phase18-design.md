# Phase 18 Test Coverage Design

## Scope

Broaden pending-update detail-fetched coverage around `on_order_details_fetched(...)` so the out-of-lock queue consumer proves it keeps processing later updates after one earlier update fails validation.

## Target behavior

1. `on_order_details_fetched()`
   - when one queued update for the fetched order is invalid and returns failure
   - and a later queued update for the same order is valid
   - the handler should continue processing the later update
   - the final order state should reflect the later successful update
   - the fetched order bucket should still be drained from `pending_updates`

## Test strategy

- Extend `tests/smoke/test_order_status_pending_updates.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one real order in `processing`.
- Queue two updates for the same order:
  - first an invalid transition (`refunding`)
  - then a valid transition (`pending_ship`)
- Trigger `on_order_details_fetched()` directly.
- Assert:
  - the final order state is `pending_ship`
  - the queue is empty afterward
  - the order bucket is removed from `pending_updates`

## Planned cases

1. detail-fetched pending processing continues after one failed queued update and still applies a later valid update

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_pending_updates.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
