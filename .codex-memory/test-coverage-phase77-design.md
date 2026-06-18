# Phase 77 Design - Account Item Operation Ownership Coverage

## Objective

Lock down account item operation routes that execute live account actions from a caller-supplied `cookie_id`.

## Scope

- `POST /items/get-all-from-account`
- `POST /items/get-by-page`
- `POST /accounts/{cid}/polish-items`

## Acceptance Criteria

- Foreign users cannot trigger item sync, paged item fetch, or item polish for another user's account.
- Owner users can still run the same operations.
- Tests use fake `XianyuLive` seams so smoke coverage does not make live network/browser calls.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Use the existing `_ensure_cookie_access(...)` route helper so behavior aligns with other cookie-scoped account routes.
- Keep response shape compatible by returning existing `success=False` payloads for route-local validation failures and only preventing foreign live actions before external side effects.
- Do not change scheduled-task execution in this phase; it is a background worker path with a separate ownership model.

## Decision Record

- Decision: Gate the three account item operation routes with `_ensure_cookie_access(...)` before reading cookie secrets or constructing `XianyuLive`.
- Rationale: The routes perform account-scoped live actions and previously trusted a raw `cookie_id`, which could allow one authenticated user to operate another user's account.
- Risk: Foreign access now returns the standard cookie-access `403` instead of a generic `success=False` message; this matches adjacent account route authorization behavior.
