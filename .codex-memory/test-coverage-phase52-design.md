# Phase 52 Design

## Goal
- Add one focused smoke regression that locks down download-token HTTP exception handling.

## Scope
- `reply_server.py`
- `tests/smoke/test_file_download_tokens.py`

## Change
- Re-raise `HTTPException` inside `GET /api/files/{file_id}/download-token` so quota and not-found style responses are not converted into 500.
- Add a smoke regression proving the route keeps its existing forbidden outcome for a missing file id.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_file_download_tokens.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager.py tests`
- `git diff --check`
