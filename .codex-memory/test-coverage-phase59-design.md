# Phase 59 Design

## Goal
- Add one focused smoke regression for default-reply clear-records ownership.

## Scope
- `tests/smoke/test_default_replies.py`

## Change
- Add a foreign-user regression for `POST /default-replies/{cid}/clear-records` so one user cannot clear another user's reply history records.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_default_replies.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
