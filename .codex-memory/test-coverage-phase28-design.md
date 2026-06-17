# Phase 28 Test Coverage Design

## Scope

Broaden no-order-id direct-resolution coverage around the missing-strong-key path so `handle_red_reminder_message(...)` proves a cancelled red reminder falls back into the pending queue when the match context cannot form a strong key.

## Target behavior

1. `handle_red_reminder_message()`
   - when `extract_order_id()` fails
   - and the red reminder is `交易关闭`
   - but `sid` / `buyer_id` / `item_id` do not form a strong match key
   - the handler should not attempt direct old-order backfill
   - the handler should preserve the event by enqueueing a pending update and red-reminder queue entry

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one old order that would otherwise look like a candidate.
- Patch:
  - `extract_order_id()` to return `None`
  - `time.time()` and `uuid.uuid4()` for deterministic temp ids
- Trigger `handle_red_reminder_message(...)` with an incomplete `match_context`.
- Assert:
  - the handler returns `True`
  - the existing old order is not updated directly
  - a temporary pending update is queued
  - the pending red-reminder queue keeps the generated temp order id

## Planned cases

1. direct cancelled red-reminder backfill skips direct resolution when the strong key is incomplete

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
