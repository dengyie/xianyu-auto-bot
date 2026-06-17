# Phase 27 Test Coverage Design

## Scope

Broaden no-order-id direct-resolution coverage around the missing-strong-key path so `handle_system_message(...)` proves a cancelled system message falls back into the pending queue when the match context cannot form a strong key.

## Target behavior

1. `handle_system_message()`
   - when `extract_order_id()` fails
   - and the resolved system status is `cancelled`
   - but `sid` / `buyer_id` / `item_id` do not form a strong match key
   - the handler should not attempt direct old-order backfill
   - the handler should preserve the event by enqueueing a pending update and system-message queue entry

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one old order that would otherwise look like a candidate.
- Patch:
  - `_resolve_system_message_status()` to return `cancelled`
  - `extract_order_id()` to return `None`
  - `time.time()` and `uuid.uuid4()` for deterministic temp ids
- Trigger `handle_system_message(...)` with an incomplete `match_context`.
- Assert:
  - the handler returns `True`
  - the existing old order is not updated directly
  - a temporary pending update is queued
  - the pending system-message queue keeps the generated temp order id

## Planned cases

1. direct cancelled system-message backfill skips direct resolution when the strong key is incomplete

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
