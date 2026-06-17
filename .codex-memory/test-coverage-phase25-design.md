# Phase 25 Test Coverage Design

## Scope

Broaden no-order-id direct-resolution coverage around the ambiguity path so `handle_system_message(...)` proves a cancelled system message will fall back into the pending queue when more than one old order matches the strong key.

## Target behavior

1. `handle_system_message()`
   - when `extract_order_id()` fails
   - and the resolved system status is `cancelled`
   - and more than one old order matches by strong match key
   - the handler should not update any old order directly
   - the handler should preserve the event by enqueueing a pending update and system-message queue entry

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed two matching old orders with eligible statuses.
- Patch:
  - `_resolve_system_message_status()` to return `cancelled`
  - `extract_order_id()` to return `None`
  - `time.time()` and `uuid.uuid4()` for deterministic temp ids
- Trigger `handle_system_message(...)`.
- Assert:
  - the handler returns `True`
  - neither old order is updated directly
  - a temporary pending update is queued
  - the pending system-message queue keeps the generated temp order id

## Planned cases

1. direct cancelled system-message backfill ambiguity falls through to pending system-message queueing

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
