# Phase 81 Design - Order List/Delete Ownership Coverage

## Objective

Lock down user-scoped order listing and deletion behavior for the order management API.

## Scope

- `GET /api/orders`
- `DELETE /api/orders/{order_id}`

## Acceptance Criteria

- A user only sees orders attached to cookies they own.
- A user cannot delete another user's order by `order_id`.
- A forbidden delete attempt does not remove or mutate the foreign order.
- The owner can still delete their own order.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Keep production behavior unchanged if the existing route and DB helper paths already enforce cookie ownership.
- Add focused smoke coverage in the existing order test module because this is order API behavior rather than delivery workflow behavior.

## Decision Record

- Decision: Add route-level smoke coverage for order list/delete ownership without changing implementation.
- Rationale: Orders are user-owned through their `cookie_id`; list and delete are high-value data surfaces that should be explicitly protected by regression tests.
- Risk: Low; the test pins the current API contract and uses the in-memory DB fixture.
