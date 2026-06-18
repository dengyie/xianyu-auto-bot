# Phase 72 Design - AI Reply Settings Ownership

## Objective

Lock the remaining AI reply account-scoped route cluster to the authenticated cookie owner:

- `GET /ai-reply-settings/{cookie_id}`
- `PUT /ai-reply-settings/{cookie_id}`
- `GET /ai-reply-settings`
- `POST /ai-reply-test/{cookie_id}`

## Current Findings

- `GET /ai-reply-settings/{cookie_id}` and `PUT /ai-reply-settings/{cookie_id}` already verify that the URL `cookie_id` belongs to `current_user`.
- `GET /ai-reply-settings` filters the aggregate result to cookies owned by `current_user`.
- `POST /ai-reply-test/{cookie_id}` only checks that `cookie_id` exists in the in-memory cookie manager and that AI is enabled. It does not verify that the cookie belongs to the authenticated user before generating a reply.

## Implementation Plan

1. Add the same cookie-owner check used by the AI settings read/write routes to `POST /ai-reply-test/{cookie_id}`.
2. Add a focused smoke regression that:
   - creates one admin-owned cookie and one regular-user-owned cookie;
   - stores AI reply settings for both;
   - verifies a foreign user cannot read, update, or test the admin cookie;
   - verifies aggregate settings only include the current user's cookie;
   - verifies the owner can read, update, list, and test their own cookie;
   - stubs AI reply generation so no external provider is called.

## Acceptance Criteria

- Foreign users receive `403` for admin-owned AI reply settings read/update/test routes.
- Aggregate AI reply settings are filtered to the authenticated user's cookies.
- Owner success paths remain functional.
- Targeted smoke, full smoke, compileall, diff hygiene, and checkpoint production review pass.

## Decision Record

- Decision: Treat the test-generation route as a cookie-scoped account operation and require the same ownership check as settings read/write.
- Rationale: Generating an AI reply can expose account configuration behavior and consume model credentials/limits. Existence in the global cookie manager is not an authorization boundary.
- Risk: Existing callers that relied on cross-account test access will now receive `403`; this matches the surrounding account-scoped API contract.
