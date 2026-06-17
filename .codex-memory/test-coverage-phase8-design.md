# Phase 8 Test Coverage Design

## Scope

Broaden order lifecycle smoke coverage around pending-message ambiguity rejection and stale pending-memory cleanup in `order_status_handler.py`.

## Target branches

1. `_select_pending_message_index(...)`
   - reject binding when multiple pending messages share the same `message_hash`
   - reject binding when multiple pending messages share the same strong match key
2. `on_order_id_extracted(...)`
   - preserve the pending queue when ambiguity prevents a safe bind
3. `cleanup_expired_pending_updates(...)`
   - remove expired pending updates, pending system messages, and pending red-reminder messages
   - preserve fresh entries in the same structures

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py` to reuse the existing fake DB and helper message factory.
- Keep tests deterministic by:
  - constructing queue contents directly
  - using explicit `match_context` dictionaries
  - patching `time.time()` during cleanup verification
- Assert both the positive cleanup effect (expired entries removed) and the safety property (fresh entries remain available).

## Planned cases

1. ambiguous `message_hash` candidates do not bind and the queued system messages remain untouched
2. ambiguous strong-key candidates without a unique hash do not bind and the queue remains intact
3. expired pending updates/system messages/red reminders are cleared while fresh ones are retained

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
