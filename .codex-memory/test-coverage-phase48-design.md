# Phase 48 Design - face-verification screenshot ownership boundaries

## Goal

Add focused route-level smoke coverage for the face-verification screenshot endpoints so only the owning user can read or delete another account's verification screenshot.

## Scope

- Extend `tests/smoke/test_accounts.py`.
- Target:
  - `GET /face-verification/screenshot/{account_id}`
  - `DELETE /face-verification/screenshot/{account_id}`

## Why This Phase

The face-verification screenshot routes read and delete sensitive verification artifacts keyed by account id.
The delete route already checks ownership, but the read route is the higher-risk exposure surface and the two routes should be locked together.

## Test Shape

- Seed a cookie/account owned by one user and create a matching screenshot file.
- Assert a foreign user cannot read the screenshot.
- Assert the owner can read it.
- Assert a foreign user cannot delete it.
- Assert the owner can delete it.

## Acceptance

- Targeted account smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-48 diff.
