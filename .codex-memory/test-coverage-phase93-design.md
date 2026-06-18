# Phase 93 Design - Admin Backup Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/backup/download`
  - `POST /admin/backup/upload`
  - `GET /admin/backup/list`

## Verifiable Result
- Regular authenticated users are rejected from backup download, upload, and list endpoints.
- Admin backup download reports `404` when the configured DB path is not a file.
- Admin backup upload rejects non-`.db` files before restore logic.
- Admin backup list remains stable when no backup files are present.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Use existing admin and regular-user auth fixtures.
- Patch `glob.glob` so backup listing does not depend on real files.
- Use an invalid extension upload to validate early rejection without mutating the DB.

## Acceptance
- Targeted security hardening test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
