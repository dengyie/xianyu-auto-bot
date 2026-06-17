# Phase 24 Test Coverage Design

## Scope

Broaden no-order-id direct-resolution coverage around the success path so `handle_system_message(...)` proves a cancelled system message can directly backfill a unique old order without entering the pending queue.

## Target behavior

1. `handle_system_message()`
   - when `extract_order_id()` fails
   - and the resolved system status is `cancelled`
   - and a unique old order is found by strong match key
   - and updating that old order succeeds
   - the handler should return `True`
   - the old order should be updated to `cancelled`
   - no pending system-message entry should be created
   - no temporary pending update should remain

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one unique old order candidate.
- Patch:
  - `_resolve_system_message_status()` to return `cancelled`
  - `extract_order_id()` to return `None`
- Trigger `handle_system_message(...)`.
- Assert:
  - the handler returns `True`
  - the old order is updated to `cancelled`
  - the pending queue remains empty
  - no system-message queue entry is created

## Planned cases

1. direct cancelled system-message backfill succeeds for the unique matching old order

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
