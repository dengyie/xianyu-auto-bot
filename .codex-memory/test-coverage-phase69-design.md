# Phase 69 Design - Item Reply Ownership And Metadata Isolation

## Objective
Lock down the item-reply route cluster so reply operations remain owner-scoped and item metadata cannot leak across cookies that share the same `item_id`.

## Scope
- Cover `GET /itemReplays/cookie/{cookie_id}`.
- Cover `GET /item-reply/{cookie_id}/{item_id}`.
- Cover `PUT /item-reply/{cookie_id}/{item_id}`.
- Cover `DELETE /item-reply/{cookie_id}/{item_id}`.
- Cover `DELETE /item-reply/batch`.
- Fix item-reply list metadata joins to bind `item_info` by both `cookie_id` and `item_id`.

## Non-Goals
- Do not change item-reply request/response shapes.
- Do not change marketplace sync behavior.
- Do not add schema migrations or deletion behavior in this phase.

## Verification Plan
- Seed owner and foreign cookies with the same `item_id` but different item metadata.
- Assert foreign users receive `403` for another user's list, read, update, delete, and batch delete routes.
- Assert the owner can list/read/update/delete and only receives the owner's item metadata.
- Run targeted smoke tests, full smoke suite, compileall, diff hygiene, and checkpoint production review.

## Expected Result
Item-reply route ownership and metadata isolation are explicitly covered, and cross-cookie same-`item_id` metadata leakage is prevented.
