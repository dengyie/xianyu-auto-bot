# Phase 10 Test Coverage Design

## Scope

Broaden order lifecycle smoke coverage around terminal recent-fallback matching for queued red-reminder messages in `order_status_handler.py`.

## Target branches

1. `on_order_id_extracted(...)` red-reminder queue handling
   - consumes a queued terminal red-reminder message when recent fallback matching finds a unique candidate
   - preserves the red-reminder queue when recent fallback matching is ambiguous
2. `_select_terminal_pending_message_index(...)`
   - reuse the same timestamp-window fallback path already covered for system messages, but through the red-reminder queue

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py` to reuse the existing fake DB and helper message factory.
- Construct queued red-reminder messages directly so the test enters the delayed-binding path without relying on live message parsing.
- Neutralize `message_hash` and strong-key selectors by giving the current message a different hash and leaving queued entries without strong-key fields.
- Keep timestamps inside the configured terminal bind gap so the code must decide between unique fallback bind and ambiguity reject.

## Planned cases

1. one queued cancelled red-reminder with matching `sid` and nearby timestamp binds through recent fallback and updates the order
2. two queued cancelled red-reminders with matching `sid` and nearby timestamps are rejected as ambiguous and remain queued

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
