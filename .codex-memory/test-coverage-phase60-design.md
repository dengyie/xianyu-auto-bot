# Phase 60 Design

## Goal
- Add one focused smoke regression for `keywords-with-item-id` ownership.

## Scope
- `tests/smoke/test_keywords_default_replies.py`

## Change
- Add a foreign-user regression for `GET /keywords-with-item-id/{cid}` and `POST /keywords-with-item-id/{cid}` so one user cannot read or overwrite another user's item-scoped keyword rules.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_keywords_default_replies.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
