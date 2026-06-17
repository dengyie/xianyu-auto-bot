# Phase 26 Test Coverage Design

## Scope

Broaden no-order-id direct-resolution coverage around the ambiguity path so `handle_red_reminder_message(...)` proves a cancelled red reminder will fall back into the pending queue when more than one old order matches the strong key.

## Target behavior

1. `handle_red_reminder_message()`
   - when `extract_order_id()` fails
   - and the red reminder is `交易关闭`
   - and the resolved status is `cancelled`
   - and more than one old order matches by strong key
   - the handler should not update any old order directly
   - the handler should preserve the event by enqueueing a pending update and red-reminder queue entry

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed two matching old orders with eligible statuses.
- Patch:
  - `extract_order_id()` to return `None`
  - `time.time()` and `uuid.uuid4()` for deterministic temp ids
- Trigger `handle_red_reminder_message(...)`.
- Assert:
  - the handler returns `True`
  - neither old order is updated directly
  - a temporary pending update is queued
  - the pending red-reminder queue keeps the generated temp order id

## Planned cases

1. direct cancelled red-reminder backfill ambiguity falls through to pending red-reminder queueing

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
