# Phase 61 Design

## Goal
- Add one focused smoke regression for cookie remark ownership.

## Scope
- `tests/smoke/test_cookie_access_control.py`

## Change
- Add a foreign-user regression for `GET /cookies/{cid}/remark` and `PUT /cookies/{cid}/remark` so one user cannot read or overwrite another user's account remark.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_cookie_access_control.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
