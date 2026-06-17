# Phase 63 Design

## Goal
- Add one focused smoke regression for cookie auto-confirm ownership.

## Scope
- `tests/smoke/test_cookie_access_control.py`

## Change
- Add a foreign-user regression for `GET /cookies/{cid}/auto-confirm` and `PUT /cookies/{cid}/auto-confirm` so one user cannot read or overwrite another user's auto-confirm setting.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_cookie_access_control.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
