# Phase 74 Design - Card And Delivery Rule Ownership

## Objective

Lock focused smoke coverage for non-cookie user-owned delivery resources:

- `GET /cards`
- `POST /cards`
- `GET /cards/{card_id}`
- `PUT /cards/{card_id}`
- `DELETE /cards/{card_id}`
- `GET /delivery-rules`
- `POST /delivery-rules`
- `GET /delivery-rules/{rule_id}`
- `PUT /delivery-rules/{rule_id}`
- `DELETE /delivery-rules/{rule_id}`

## Current Findings

- Card and delivery-rule data-layer helpers accept `user_id` and route mutations already pass the authenticated user's id.
- `GET /cards/{card_id}` and `GET /delivery-rules/{rule_id}` raise `HTTPException(404)` for a missing or foreign record, but broad `except Exception` handlers convert that into `500`.
- Existing smoke coverage does not directly prove cross-user denial or list filtering for cards and delivery rules.

## Implementation Plan

1. Preserve explicit `HTTPException` status codes in the single-card and single-delivery-rule read routes.
2. Add smoke coverage that:
   - creates owner and foreign cards;
   - verifies list filtering by current user;
   - verifies foreign users cannot read, update, delete owner cards;
   - verifies foreign users cannot create rules with another user's card;
   - creates an owner delivery rule and verifies list/read/update/delete owner paths;
   - verifies foreign users cannot read, update, or delete the owner delivery rule.

## Acceptance Criteria

- Foreign read/update/delete attempts for cards and delivery rules return `404`, not `500`.
- Foreign delivery-rule creation with another user's card returns `404`.
- Owner card and delivery-rule success paths remain functional.
- Targeted smoke, full smoke, compileall, diff hygiene, and checkpoint production review pass.

## Decision Record

- Decision: Use `404` for foreign card/rule reads and mutations, matching existing helper behavior that treats missing and unauthorized user-scoped resources uniformly.
- Rationale: Hiding whether a resource id exists outside the current user's scope reduces cross-user enumeration while preserving the API contract already used by update/delete helpers.
- Risk: Clients that previously observed `500` for foreign reads will now receive the intended `404`; this is a corrective contract change.
