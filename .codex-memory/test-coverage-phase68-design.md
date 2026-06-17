# Phase 68 Design - Item Info Ownership Smoke Coverage

## Objective
Add focused smoke coverage for the item information route cluster so item list/detail/update/delete operations remain scoped to the cookie owner.

## Scope
- Cover `GET /items/cookie/{cookie_id}`.
- Cover `GET /items/{cookie_id}/{item_id}`.
- Cover `PUT /items/{cookie_id}/{item_id}`.
- Cover `DELETE /items/{cookie_id}/{item_id}`.

## Non-Goals
- Do not change item synchronization or marketplace fetch behavior.
- Do not broaden the production route surface.
- Do not cover batch item deletion in this phase.

## Verification Plan
- Seed one owner cookie and item through existing API/data-layer helpers.
- Assert a different authenticated user receives `403` for list/detail/update/delete.
- Assert the owner can list/read/update/delete the same item.
- Run targeted smoke tests, full smoke suite, compileall, diff hygiene, and checkpoint production review.

## Expected Result
The smoke suite explicitly protects the item-info owner boundary while preserving the existing runtime behavior.
