# Phase 73 Design - Account Runtime Route Ownership

## Objective

Lock focused smoke coverage for the remaining account-runtime and sensitive account-configuration route cluster:

- `POST /cookie/{cid}/account-info`
- `GET /cookie/{cid}/details`
- `GET /cookies/{cid}/runtime-status`
- `GET /cookies/{cid}/conversations/{conversation_id}/history`
- `POST /cookies/{cid}/session-keepalive`
- `GET /cookie/{cid}/proxy`
- `POST /cookie/{cid}/proxy`

## Current Findings

- The route implementations already enforce ownership through `_ensure_cookie_access(...)` or `db_manager.get_all_cookies(user_id)` checks.
- Existing smoke coverage only pins a narrow foreign-user denial for proxy-secret reads.
- The runtime/history/keepalive surfaces were not yet covered by focused cross-user route tests, even though they can reveal operational account state or trigger account-side activity.

## Implementation Plan

1. Add a smoke regression that creates one admin-owned cookie and one regular-user-owned cookie.
2. Verify a foreign user cannot:
   - update account info;
   - read unmasked account details;
   - read runtime status;
   - read conversation history;
   - trigger session keepalive;
   - read or update proxy settings.
3. Verify owner success paths still work for the same route cluster.
4. Stub live-instance operations for history and keepalive so the smoke test does not depend on browser/network/runtime state.

## Acceptance Criteria

- Foreign-user access to every covered account-runtime/config route returns `403`.
- Owner account-info and proxy updates persist.
- Owner details can be read and include the expected sensitive fields when explicitly requested.
- Owner history and keepalive routes return deterministic success through local stubs.
- Targeted smoke, full smoke, compileall, diff hygiene, and checkpoint production review pass.

## Decision Record

- Decision: Treat account runtime/status/history/keepalive routes as a single Phase 73 route cluster and cover them with one focused integration-style smoke test.
- Rationale: These routes share the same account ownership boundary and are adjacent operational surfaces rather than independent business workflows. One route-cluster test keeps coverage tight while avoiding duplicated setup.
- Risk: The test stubs live runtime calls, so it proves route ownership and response contracts rather than real browser/session behavior. Runtime behavior is covered by separate XianyuLive smoke tests.
