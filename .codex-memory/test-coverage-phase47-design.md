# Phase 47 Design - qr-login session ownership boundaries

## Goal

Add focused route-level smoke coverage for the standard QR login session status endpoint and make the session ownership explicit in the in-memory session model.

## Scope

- Update `utils/qr_login.py` to retain the session owner on generated QR login sessions.
- Update `reply_server.py` to reject foreign users from `GET /qr-login/check/{session_id}`.
- Extend `tests/smoke/test_accounts.py`.

## Why This Phase

The standard QR login flow already records a session id and status, but it does not persist the creating user on the session object.
That leaves the status endpoint unable to enforce the same ownership boundary already used by password-login and qr-login-lite.

## Test Shape

- Seed or create a QR login session owned by one user.
- Query that session from a different user and assert the route rejects access.
- Confirm the owner can still query the session afterward.

## Acceptance

- Targeted account smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-47 diff.
