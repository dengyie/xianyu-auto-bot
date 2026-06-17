# Phase 14 Test Coverage Design

## Scope

Broaden delayed terminal-message binding coverage around the discard path where a pending terminal message should be treated as already consumed by another recently resolved order with the same strong match key.

## Target branches

1. `on_order_id_extracted(...)` + `_pending_terminal_message_already_resolved(...)`
   - system-message queue candidate is terminal
   - candidate is not bindable to the newly extracted order
   - another recent order with the same strong key already has a resolution status that consumes this pending terminal message
   - candidate should be discarded and its temporary pending update cleared
2. same behavior for red-reminder queue

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py`.
- Reuse the fake DB and helper message factory.
- Seed:
  - a current extracted order still in a non-terminal state
  - a different order with the same strong key already in a terminal resolution status
  - one queued terminal pending message whose timestamp gap is too large to bind to the new order
- Assert that the queue entry is discarded and its temp pending update is removed.

## Planned cases

1. `refund_cancelled` system-message candidate is discarded when another matching order already reflects the consumed terminal outcome
2. `cancelled` red-reminder candidate is discarded when another matching order already reflects the consumed terminal outcome

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
