# Phase 7 Test Coverage Design

## Scope

Broaden order lifecycle smoke coverage around delayed system-message binding and unmatched cancellation resolution in `order_status_handler.py`.

## Target branches

1. `handle_system_message(...)`
   - queues terminal system messages without an order id into `_pending_system_messages`
2. `handle_red_reminder_message(...)`
   - queues cancelled red-reminder messages without an order id into `_pending_red_reminder_messages`
   - directly resolves a cancelled message without an order id when strong match context finds exactly one recent order
3. `on_order_id_extracted(...)`
   - binds a queued system message to the extracted order id and consumes the temp pending update
   - discards a queued terminal message when another order with the same strong match context has already reached a terminal resolution

## Test strategy

- Keep tests deterministic by instantiating `OrderStatusHandler` directly.
- Patch `db_manager.db_manager` with a focused fake that implements:
  - `get_order_by_id`
  - `insert_or_update_order`
  - `get_order_pre_refund_status`
  - `find_recent_orders_by_match_context`
- Use explicit `match_context` values (`sid`, `buyer_id`, `item_id`) so tests exercise the strict binding paths instead of fragile message parsing.
- Use synthetic timestamps to cover terminal-message binding and discard behavior without waiting on real time.

## Planned cases

1. system message without order id gets queued with a temp pending update
2. later `on_order_id_extracted(...)` binds the queued system message and updates the real order
3. queued cancelled red-reminder is discarded when another matching order is already resolved as cancelled
4. cancelled red-reminder without order id directly resolves to the single matching historical order

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
