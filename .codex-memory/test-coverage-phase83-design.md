# Phase 83 Design - Cookie Availability Check User Scope

## Objective

Prevent `/cookies/check` from exposing global account availability counts to anonymous or unrelated users.

## Scope

- `GET /cookies/check`

## Acceptance Criteria

- Anonymous requests do not reveal global cookie counts.
- Authenticated users only see availability counts for cookies they own.
- Enabled and valid counts respect the current user's cookie set.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Keep the endpoint permissive for anonymous search UI compatibility, but return zero counts when no user is authenticated.
- When authenticated, call `db_manager.get_all_cookies(current_user["user_id"])` instead of the global cookie list.
- Reuse the existing cookie-manager status check so enabled/valid semantics remain unchanged.

## Decision Record

- Decision: Scope `/cookies/check` counts to the current user and return empty availability for anonymous callers.
- Rationale: Global cookie counts reveal cross-user operational state and can incorrectly make a regular user's UI depend on another user's configured accounts.
- Risk: Low; callers only need to know whether their own usable cookies exist.
