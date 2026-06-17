# Phase 19 Test Coverage Design

## Scope

Broaden direct system-message handling coverage around the status-priority rollback guard so `handle_system_message(...)` proves it will ignore lower-priority updates for an already advanced order.

## Target behavior

1. `handle_system_message()`
   - when the message resolves to a lower-priority non-exception status
   - and the target order already holds a higher-priority status
   - the handler should treat the message as handled without rolling the order backward
   - the stored order status should remain unchanged

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the existing fake DB manager and `OrderStatusHandler`.
- Seed one order already in `shipped`.
- Patch `_resolve_system_message_status()` to return `pending_ship`.
- Patch `extract_order_id()` to return that seeded order id directly.
- Trigger `handle_system_message(...)`.
- Assert:
  - the handler returns `True`
  - the order status stays `shipped`
  - no pending queue entries are created

## Planned cases

1. direct system-message handling ignores a lower-priority rollback update when the order is already in a later state

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
