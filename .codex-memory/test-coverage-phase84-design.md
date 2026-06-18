# Phase 84 Design - System Cache Reload Admin Boundary

## Objective

Restrict global cache reload operations to administrators.

## Scope

- `POST /system/reload-cache`

## Acceptance Criteria

- Regular authenticated users receive `403`.
- Admin users can still trigger `cookie_manager.manager.reload_from_db()`.
- Explicit `HTTPException` status codes from the route are preserved.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- `reload_from_db()` refreshes global `CookieManager` state and is presented in the UI as "系统缓存管理", so it should align with other system-level admin surfaces.
- Use `require_admin` for compatibility with both `is_admin=True` and legacy `username == "admin"` behavior.

## Decision Record

- Decision: Change `/system/reload-cache` from authenticated-user access to admin-only access.
- Rationale: A regular user should not be able to refresh global runtime cache state for all accounts.
- Risk: Low; this is a system settings/admin page operation and admin success behavior remains unchanged.
