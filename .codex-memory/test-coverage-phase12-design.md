# Phase 12 Test Coverage Design

## Scope

Broaden delayed order-status queue coverage so real enqueue entrypoints clean stale in-memory pending state before appending new unresolved messages.

## Change

1. `handle_system_message(...)`
   - call `clear_old_pending_updates()` before appending a new unresolved system message
2. `handle_red_reminder_message(...)`
   - call `clear_old_pending_updates()` before appending a new unresolved red-reminder message

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Seed each queue with one expired item and one fresh item.
- Trigger the real enqueue entrypoint with a new unresolved message.
- Assert that:
  - the expired item is removed
  - the fresh item remains
  - the new queue item is appended
  - the corresponding expired pending-update entry is removed too

## Planned cases

1. unresolved system-message enqueue clears stale pending state before appending a new queue entry
2. unresolved red-reminder enqueue clears stale pending state before appending a new queue entry

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
