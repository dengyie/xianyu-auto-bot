# Phase 86 Design - Sales Statistics User Scope Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /api/sales`
  - `GET /api/sales/summary`

## Verifiable Result
- Anonymous callers are rejected.
- Authenticated users only see sales totals from orders attached to their own cookies.
- Foreign-user orders with eligible statuses are excluded from both daily sales and summary totals.
- Own orders with ineligible statuses or invalid amounts are excluded from totals.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Seed two users' cookies and orders through the existing DB helpers.
- Query a fixed date range for `/api/sales` so assertions are deterministic.
- Query `/api/sales/summary` with current-month order timestamps so the summary includes the seeded own order.

## Acceptance
- Targeted authz matrix test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
