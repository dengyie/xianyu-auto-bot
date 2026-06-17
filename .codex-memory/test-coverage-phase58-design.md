# Phase 58 Design

## Goal
- Add one focused smoke regression for notification test-send success.

## Scope
- `tests/smoke/test_notifications.py`

## Change
- Add a regression for `POST /notification-templates/test` proving the current user can send a test notification through their own enabled channel without depending on another user's configuration.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_notifications.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
