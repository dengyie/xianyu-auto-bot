# Phase 65 Design

## Goal
- Harden comment-template mutations so `template_id` must belong to the URL `cid`.

## Scope
- `reply_server.py`
- `db_manager.py`
- `db_manager/accounts.py`
- `tests/smoke/test_cookie_access_control.py`

## Change
- Pass `cid` into comment-template update/delete calls and make the data layer reject mismatched template ownership.
- Make activation fail without mutating existing active templates when the target template does not belong to the URL `cid`.
- Add a smoke regression proving a user cannot update, delete, or activate another cookie's comment template by combining their own `cid` with a foreign `template_id`.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_cookie_access_control.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py db_manager tests`
- `git diff --check`
