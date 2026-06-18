# Phase 88 Design - Admin Cookies Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/cookies`

## Verifiable Result
- Regular authenticated users are rejected.
- Admin users can list cookie metadata across users for operational support.
- The response includes only metadata needed by the admin view and does not expose raw cookie values.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Seed admin-owned and regular-user-owned cookies with remarks.
- Replace the runtime cookie manager with a minimal fake status provider for deterministic enabled flags.
- Assert regular-user denial, admin success, cross-user metadata presence, and raw cookie value absence.

## Acceptance
- Targeted authz matrix test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
