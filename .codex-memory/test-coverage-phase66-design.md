# Phase 66 Design

## Goal
- Add focused smoke coverage for comment-template list/create cookie ownership.

## Scope
- `tests/smoke/test_cookie_access_control.py`

## Change
- Add a regression proving `GET /cookies/{cid}/comment-templates` and `POST /cookies/{cid}/comment-templates` reject a foreign user.
- Keep owner success in the same test so the route contract remains covered for normal use.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_cookie_access_control.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py db_manager tests`
- `git diff --check`
