# Phase 13 Test Coverage Design

## Scope

Broaden delayed terminal-message binding coverage around the guard that refuses to bind an old terminal pending message to a newly extracted order when their timestamps are too far apart.

## Target branches

1. `on_order_id_extracted(...)` + `_should_bind_pending_terminal_message(...)`
   - system-message queue candidate matches selector keys
   - terminal pending status is selected
   - timestamp gap exceeds `pending_terminal_bind_max_gap_seconds`
   - message must remain queued instead of being bound or discarded
2. `on_order_id_extracted(...)` + `_should_bind_pending_terminal_message(...)`
   - same behavior for red-reminder queue

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the fake DB and helper message factory.
- Seed a queue with one terminal pending candidate that uniquely matches by `message_hash`.
- Use a far-future extracted-order message timestamp so `_should_bind_pending_terminal_message(...)` rejects binding.
- Keep the fake DB free of alternate resolved orders so `_pending_terminal_message_already_resolved(...)` does not discard the candidate.

## Planned cases

1. terminal system-message candidate remains queued when bind gap is too large
2. terminal red-reminder candidate remains queued when bind gap is too large

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
