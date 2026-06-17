# Phase 22 Test Coverage Design

## Scope

Broaden no-order-id direct-resolution coverage around the fallback path so `handle_red_reminder_message(...)` proves a failed old-order backfill will fall through into the pending queue instead of silently dropping the event.

## Target behavior

1. `handle_red_reminder_message()`
   - when `extract_order_id()` fails
   - and a unique old order is found by strong match key
   - but updating that old order fails
   - the handler should keep the event by enqueueing a new pending update and red-reminder queue entry

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one unique old order candidate.
- Patch:
  - `extract_order_id()` to return `None`
  - `update_order_status()` to return `False`
  - `time.time()` and `uuid.uuid4()` for deterministic temp ids
- Trigger `handle_red_reminder_message(...)`.
- Assert:
  - the handler returns `True`
  - the old order state stays unchanged
  - a new temporary pending update is queued
  - the red-reminder pending queue keeps the generated temp order id

## Planned cases

1. direct cancelled-message backfill failure falls through to pending red-reminder queueing

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
