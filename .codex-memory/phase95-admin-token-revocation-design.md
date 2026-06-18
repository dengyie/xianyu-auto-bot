# Phase 95 Design - Admin Token Revocation On Permission Change

## Scope
- Fix the P1 production review finding where an already-issued admin token keeps admin access after the user's admin status is revoked.
- Cover both admin dependency paths:
  - `verify_admin_token`
  - `require_admin`

## Verifiable Result
- A token issued while a user is admin is rejected from admin endpoints after that user's `is_admin` flag is changed to false.
- Admin status changes remove existing session tokens for the changed user.
- Ordinary authenticated-user routes still work from valid non-admin tokens.

## Implementation Plan
- Rehydrate token user data from the current database row in `verify_token`.
- Keep token expiry behavior unchanged.
- If the user row no longer exists or is inactive, remove the token and treat it as unauthenticated.
- When `/admin/users/{user_id}/admin-status` succeeds, remove existing `SESSION_TOKENS` for that `user_id`.

## Acceptance
- Add focused smoke coverage for stale admin token revocation.
- Targeted authz/security smoke tests pass.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
