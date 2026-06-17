# Phase 45 Design - password-login session ownership and terminal cancellation

## Goal

Add focused route-level smoke coverage for password-login session status and cancellation when another user tries to access the session.

## Scope

- Extend `tests/smoke/test_accounts.py`.
- Reuse the existing account-management smoke fixtures.
- Target:
  - `GET /password-login/check/{session_id}`
  - `POST /password-login/cancel/{session_id}`

## Why This Phase

The password-login flow already has session lifecycle code in production, but there is no direct smoke coverage proving that a session remains scoped to its creator.
That matters because the route returns verification and error details, and cancellation mutates a shared session object plus the related risk-log state.

## Test Shape

- Create a password-login session owned by one user.
- Attempt to query and cancel that session from a different user and assert the routes reject access.
- Confirm the owner can still query the session after denied foreign access attempts.

## Acceptance

- Targeted account smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-45 diff.
