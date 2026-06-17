# Phase 55 Design

## Goal
- Add one focused smoke regression for message-notification account-delete ownership.

## Scope
- `tests/smoke/test_notifications.py`

## Change
- Add a foreign-user regression for `DELETE /message-notifications/account/{cid}` so one user cannot clear another user's per-account notification configuration.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_notifications.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
