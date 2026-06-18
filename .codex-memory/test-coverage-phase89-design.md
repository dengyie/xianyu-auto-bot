# Phase 89 Design - Admin User Management Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/users`
  - `DELETE /admin/users/{user_id}`
  - `PUT /admin/users/{user_id}/admin-status`

## Verifiable Result
- Regular authenticated users are rejected from user-management list and mutation endpoints.
- Admin list responses omit password hashes while including operational counts.
- Admins cannot delete themselves.
- Admins cannot change their own admin status.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Reuse existing admin and regular-user auth fixtures.
- Assert regular-user `403` on list/delete/admin-status mutations.
- Assert admin list succeeds and does not include password hash fields.
- Assert self-protection returns `400` for delete and admin-status mutation.

## Acceptance
- Targeted authz matrix test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
