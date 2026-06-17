# Phase 17 Test Coverage Design

## Scope

Broaden pending-update batch processing coverage around `process_all_pending_updates(...)` so the batch drain path proves one failing order bucket does not prevent later queued orders from being processed.

## Target behavior

1. `process_all_pending_updates()`
   - when one queued order bucket still cannot be applied because the order is missing
   - and another queued order bucket is processable
   - the handler should keep processing the later bucket
   - the return value should count only the successfully processed order buckets
   - the failed bucket should remain queued through the existing requeue path

## Test strategy

- Extend `tests/smoke/test_order_status_pending_updates.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one existing order and one missing order in `pending_updates`.
- Trigger `process_all_pending_updates()` directly.
- Assert:
  - the existing order is updated
  - the return value reflects only the successful bucket
  - the missing order remains in `pending_updates`
  - the requeued missing order keeps the expected target status

## Planned cases

1. batch pending-update processing continues after one bucket fails and leaves the failed bucket queued

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_pending_updates.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
