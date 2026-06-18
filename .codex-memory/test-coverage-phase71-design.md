# Phase 71 Design - Chat Keyword Item Route Ownership

## Objective
Add focused smoke coverage for chat item keyword routes so their cookie ownership contract stays pinned.

## Scope
- Cover `GET /api/chat/keywords/{cid}/item/{item_id}`.
- Cover `POST /api/chat/keywords/{cid}/item/{item_id}`.
- Cover `POST /api/chat/keywords/{cid}/copy`.
- Cover `GET /api/chat/items/{cid}`.

## Non-Goals
- Do not change the current item-existence contract for keyword copy targets.
- Do not change keyword request or response shapes.
- Do not refactor keyword storage helpers.

## Verification Plan
- Seed owner and foreign cookies through existing API helpers.
- Seed owner item metadata and item-scoped keywords/reply through existing data-layer helpers.
- Assert a different authenticated user receives `403` for the chat keyword item route cluster.
- Assert the owner can list items, read item keywords/reply, save item keywords/reply, and copy keywords/reply to another item under the same cookie.
- Run targeted smoke tests, full smoke suite, compileall, diff hygiene, and checkpoint production review.

## Expected Result
The smoke suite explicitly protects the chat keyword item route cluster without changing production behavior.
