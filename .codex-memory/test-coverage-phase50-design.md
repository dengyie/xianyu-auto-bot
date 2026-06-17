# Phase 50 Design

## Goal
Cover unmatched cancellation resolution when terminal cancellation messages have strong match keys but no historical order candidates.

## Scope
- Add smoke coverage for red reminder `交易关闭` with no extracted order ID and zero match-context candidates.
- Add smoke coverage for cancelled system messages with no extracted order ID and zero match-context candidates.
- Assert both paths fall back to strict pending queues instead of mutating unrelated orders.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_order_status_message_binding.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests`
- `git diff --check`
