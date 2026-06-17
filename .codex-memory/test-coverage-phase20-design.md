# Phase 20 Test Coverage Design

## Scope

Broaden delayed-binding terminal discard coverage around the `completed` resolution branch so `on_order_id_extracted(...)` proves already-consumed completion messages are discarded instead of rebound onto a newer order.

## Target behavior

1. `on_order_id_extracted()`
   - when a pending terminal system message has `new_status == "completed"`
   - and another recent order with the same strong match key is already in a completion-compatible resolved state
   - the handler should discard the pending message
   - the new order should remain unchanged
   - the temporary pending update should be cleaned up

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed:
  - one new processing order
  - one already completed order with the same strong match key
  - one queued pending system message with `new_status == "completed"`
  - one matching temporary pending update entry
- Trigger `on_order_id_extracted(...)`.
- Assert:
  - the new order remains `processing`
  - the pending system-message queue is emptied
  - the temporary pending update is removed

## Planned cases

1. completed terminal system message is discarded when another recent order already consumed that outcome

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
