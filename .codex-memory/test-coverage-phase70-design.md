# Phase 70 Design - Item Flag Ownership

## Objective
Require cookie ownership before updating item-level multi-spec and multi-quantity delivery flags.

## Scope
- Fix `PUT /items/{cookie_id}/{item_id}/multi-spec`.
- Fix `PUT /items/{cookie_id}/{item_id}/multi-quantity-delivery`.
- Add focused smoke coverage for foreign-user denial and owner success.

## Non-Goals
- Do not change item flag request or response shapes.
- Do not change item synchronization or delivery behavior.
- Do not add read endpoints for these flags.

## Verification Plan
- Seed an owner cookie and item through existing helpers.
- Assert a different authenticated user receives `403` for both flag update routes.
- Assert the owner can update both flags and the database reflects the owner updates.
- Run targeted smoke tests, full smoke suite, compileall, diff hygiene, and checkpoint production review.

## Expected Result
Item flag mutations are denied before data-layer mutation when the cookie belongs to another user, while owner behavior remains unchanged.
