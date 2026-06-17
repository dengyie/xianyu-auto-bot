# Phase 9 Test Coverage Design

## Scope

Broaden order lifecycle smoke coverage around terminal recent-match fallback selection in `order_status_handler.py`.

## Target branches

1. `_select_terminal_pending_message_index(...)`
   - unique fallback match through recent terminal timestamp window
   - ambiguity rejection when multiple terminal candidates match the same fallback fields inside the allowed gap
2. `on_order_id_extracted(...)`
   - consumes a queued terminal system message when fallback recent matching finds one unique candidate
   - preserves the queue when fallback recent matching is ambiguous

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py` to reuse the existing fake DB and helper message factory.
- Avoid message-hash and strong-key matches in these tests so the code must enter the terminal recent fallback path.
- Keep timestamps within the configured terminal bind gap for the positive and ambiguous cases.
- Assert the observable behavior:
  - unique fallback match updates the order and drains the queue
  - ambiguous fallback match leaves the order unchanged and keeps the queue intact

## Planned cases

1. one queued terminal message with matching `sid` and nearby timestamp binds through `terminal_sid_recent`
2. two queued terminal messages with matching `sid` and nearby timestamps are rejected as ambiguous and remain queued

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
