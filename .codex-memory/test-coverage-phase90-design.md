# Phase 90 Design - Admin Log Access Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/logs`
  - `GET /admin/log-files`
  - `GET /admin/logs/export`

## Verifiable Result
- Regular authenticated users are rejected from log read, log file listing, and log export endpoints.
- Admin log listing remains stable when no runtime log files exist.
- Admin log export returns `404` for a missing log file instead of exposing filesystem content.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Patch `glob.glob` for deterministic no-log behavior in `/admin/logs`.
- Exercise the three endpoints with regular-user credentials for `403`.
- Exercise admin read/list/missing-export outcomes without creating or deleting real log files.

## Acceptance
- Targeted authz matrix test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
