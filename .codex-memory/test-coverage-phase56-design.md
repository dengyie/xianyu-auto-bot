# Phase 56 Design

## Goal
- Add one focused smoke regression for single message-notification delete ownership.

## Scope
- `tests/smoke/test_notifications.py`

## Change
- Add a foreign-user regression for `DELETE /message-notifications/{notification_id}` so one user cannot delete another user's individual notification entry.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_notifications.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
