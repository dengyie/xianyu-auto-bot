# Phase 91 Design - Admin System Stats Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/stats`

## Verifiable Result
- Regular authenticated users are rejected.
- Admin users can read system-wide aggregate counts.
- User, cookie, and card totals include records across users.
- Card enabled counts reflect enabled and disabled cards.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Seed admin/user cookies and admin/user cards through existing DB helpers.
- Assert regular-user `403`.
- Assert admin response includes global user, cookie, and card totals plus version metadata.

## Acceptance
- Targeted authz matrix test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
