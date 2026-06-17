# Phase 11 Test Coverage Design

## Scope

Broaden order lifecycle smoke coverage around the `message_hash+strong_key` disambiguation path in `order_status_handler.py`.

## Target branches

1. `_select_pending_message_index(...)`
   - returns a unique candidate through `message_hash+strong_key` when duplicate message hashes exist
2. `on_order_id_extracted(...)`
   - consumes the uniquely disambiguated pending system message
   - consumes the uniquely disambiguated pending red-reminder message

## Test strategy

- Extend `tests/smoke/test_order_status_message_binding.py` to reuse the existing fake DB and helper message factory.
- Construct queues where:
  - two pending entries share the same `message_hash`
  - only one entry matches the current strong key (`sid`, `buyer_id`, `item_id`)
- Assert that the right entry is consumed and the non-matching sibling remains queued.

## Planned cases

1. system-message queue resolves duplicate `message_hash` candidates through a unique strong-key match
2. red-reminder queue resolves duplicate `message_hash` candidates through a unique strong-key match

## Verification

- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests order_status_handler.py`
