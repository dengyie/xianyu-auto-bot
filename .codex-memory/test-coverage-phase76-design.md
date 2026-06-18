# Phase 76 Design - Update API Admin Boundary Coverage

## Objective

Lock down the admin boundary for high-risk update-management APIs without changing the existing authenticated read-only update check/progress behavior.

## Scope

- `POST /api/update/apply`
- `GET /api/update/local-hashes`
- `POST /api/update/cleanup-backups`
- `GET /api/update/file-changes`
- `POST /api/update/save-hashes`
- `GET /api/update/saved-hashes`
- `POST /api/update/restart`

## Acceptance Criteria

- Regular users receive `403` for each admin-only update-management endpoint.
- Admin users identified by `is_admin=True` are accepted even when their username is not literally `admin`.
- Historical `username == "admin"` compatibility remains accepted for update-management endpoints.
- Admin success smoke coverage uses fake updater/restart seams so tests do not touch network, filesystem updates, or process restart.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Keep `GET /api/update/check` and `GET /api/update/progress` out of scope because current implementation only requires authentication, not admin, and the TODO is focused on owner/scoped risk rather than broad product-policy changes.
- Add a small route-local helper that mirrors the existing `require_admin` compatibility rule: `is_admin=True` or `username == "admin"`.
- Do not invoke the real updater or restart path in smoke tests.

## Decision Record

- Decision: Normalize update-management authorization through a helper instead of changing all route dependencies.
- Rationale: This keeps the patch small, preserves existing route shapes, and avoids accidental behavior changes for non-admin update check/progress routes.
- Risk: The helper duplicates the `require_admin` rule; this is acceptable in this phase because the update routes already had local checks and the goal is to make those local checks consistent.
