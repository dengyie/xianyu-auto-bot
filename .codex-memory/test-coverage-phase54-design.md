# Phase 54 Design

## Goal
- Add one focused smoke regression for message-notification read ownership.

## Scope
- `tests/smoke/test_notifications.py`

## Change
- Add a foreign-user regression for `GET /message-notifications/{cid}` so per-account notification entries stay scoped to the owning user.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_notifications.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
