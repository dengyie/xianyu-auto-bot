# Phase 46 Design - manual-cookie-import session ownership boundaries

## Goal

Add focused route-level smoke coverage for the manual cookie import session status endpoint when another user tries to inspect the session.

## Scope

- Extend `tests/smoke/test_accounts.py`.
- Reuse the existing account-management smoke fixtures.
- Target:
  - `GET /manual-cookie-import/check/{session_id}`

## Why This Phase

The manual cookie import flow uses the same session-scoped status pattern as password-login, but current smoke coverage only checks request validation.
The adjacent production contract is the ownership boundary on the created import session itself.

## Test Shape

- Seed a manual cookie import session owned by one user.
- Attempt to query that session from a different user and assert the route rejects access.
- Confirm the owner can still query the session after the denied foreign access attempt.

## Acceptance

- Targeted account smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-46 diff.
