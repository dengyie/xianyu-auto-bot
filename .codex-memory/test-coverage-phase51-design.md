# Phase 51 Design

## Goal
Harden qr-login cooldown management routes with ownership checks and add regression smoke coverage.

## Scope
- Reject foreign-user access to `POST /qr-login/reset-cooldown/{cookie_id}`.
- Reject foreign-user access to `GET /qr-login/cooldown-status/{cookie_id}`.
- Keep owner behavior unchanged and preserve existing cooldown semantics.

## Verification
- `python -m pytest -p no:cacheprovider tests/smoke/test_accounts.py -q`
- `python -m pytest -p no:cacheprovider tests/smoke -q`
- `python -m compileall -q reply_server.py XianyuAutoAsync.py db_manager tests`
- `git diff --check`
