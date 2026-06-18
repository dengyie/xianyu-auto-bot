# Phase 82 Design - Realtime Log API Admin Boundary

## Objective

Lock down legacy realtime log APIs so regular users cannot read system logs, inspect log statistics, or clear log buffers.

## Scope

- `GET /logs`
- `GET /logs/stats`
- `POST /logs/clear`

## Acceptance Criteria

- Regular authenticated users receive `403` for all three realtime log APIs.
- Admin users can still read logs, inspect stats, and clear the realtime log buffer.
- Existing admin log routes under `/admin/logs` remain unchanged.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- These endpoints expose system-wide operational data and destructive clearing behavior, and the project already has admin-only `/admin/logs` routes for the same domain.
- Use the existing `require_admin` dependency to match the rest of the admin surface and preserve legacy username compatibility.

## Decision Record

- Decision: Convert legacy realtime log APIs from authenticated-user access to admin-only access.
- Rationale: System logs may include operational metadata, request context, and failure details across users; clearing logs is a system-level action.
- Risk: Low for the current UI because the log management frontend already calls `/admin/logs` and related admin endpoints.
