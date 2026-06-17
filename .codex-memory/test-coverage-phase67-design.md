# Phase 67 Design

## Goal
- Add focused smoke coverage for `GET /keywords-with-type/{cid}` ownership.

## Scope
- `tests/smoke/test_keywords_default_replies.py`

## Change
- Add a regression proving a foreign user cannot read another user's typed keyword list.
- Keep owner success in the same test so the typed keyword response contract remains covered.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_keywords_default_replies.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py db_manager tests`
- `git diff --check`
