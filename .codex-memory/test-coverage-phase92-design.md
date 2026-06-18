# Phase 92 Design - Admin Data Management Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/data/{table_name}`
  - `GET /admin/data/{table_name}/export`
  - `DELETE /admin/data/{table_name}`

## Verifiable Result
- Regular authenticated users are rejected from table reads, exports, and destructive clears.
- Admin access to disallowed table names returns `400`.
- Admin attempts to clear the `users` table return `400`.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Exercise representative read/export/clear endpoints with regular-user credentials.
- Exercise disallowed table and protected users-table clear with admin credentials.

## Acceptance
- Targeted security hardening test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
