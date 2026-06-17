# Phase 57 Design

## Goal
- Add one focused smoke regression for notification test-send channel scoping.

## Scope
- `tests/smoke/test_notifications.py`

## Change
- Add a regression for `POST /notification-templates/test` proving a user cannot rely on another user's enabled notification channel when they have none of their own.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_notifications.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
