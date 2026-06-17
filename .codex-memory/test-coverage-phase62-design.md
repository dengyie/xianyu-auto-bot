# Phase 62 Design

## Goal
- Add one focused smoke regression for cookie pause-duration ownership.

## Scope
- `tests/smoke/test_cookie_access_control.py`

## Change
- Add a foreign-user regression for `GET /cookies/{cid}/pause-duration` and `PUT /cookies/{cid}/pause-duration` so one user cannot read or overwrite another user's auto-reply pause duration.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_cookie_access_control.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
