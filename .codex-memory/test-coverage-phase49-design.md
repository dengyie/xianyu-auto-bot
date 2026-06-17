# Phase 49 Design - qr-login refresh-cookies ownership boundaries

## Goal

Add focused route-level smoke coverage for the qr-login refresh-cookies endpoint so only the owning user can trigger a QR-cookie refresh for a cookie account.

## Scope

- Extend `tests/smoke/test_accounts.py`.
- Target:
  - `POST /qr-login/refresh-cookies`

## Why This Phase

The refresh-cookies route performs a high-risk cookie refresh and database write flow using only a cookie id and request payload.
It should be covered like the other account-scoped cookie routes so a foreign user cannot drive refresh work for someone else.

## Test Shape

- Seed a cookie owned by one user.
- Attempt the refresh route from a different user and assert it rejects access.
- Confirm the owner can still trigger the refresh path.

## Acceptance

- Targeted account smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-49 diff.
