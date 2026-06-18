# Phase 78 Design - Chat Runtime API Ownership Coverage

## Objective

Lock down the online chat runtime API routes that read local chat state or send live messages using a caller-supplied `cookie_id`.

## Scope

- `GET /api/chat/sessions`
- `GET /api/chat/messages`
- `POST /api/chat/send`

## Acceptance Criteria

- Foreign users cannot list chat sessions, read chat messages, or send live chat messages for another user's account.
- Owner users can still list sessions, read messages, and send through their own connected live instance.
- Tests use local DB rows and fake `XianyuLive` seams so smoke coverage does not require a real websocket or network call.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Keep production behavior unchanged because the routes already call `_ensure_cookie_access(...)`.
- Add focused route-level smoke coverage so the existing owner gate stays pinned for both read and send paths.
- Avoid testing `/api/chat/stream` in this phase because it subscribes by authenticated user id rather than a caller-supplied `cookie_id`.

## Decision Record

- Decision: Add regression coverage for the chat sessions, messages, and send routes without changing route implementation.
- Rationale: These routes are a high-value account-scoped API cluster, and the current authorization behavior is correct but unpinned by focused cross-user smoke tests.
- Risk: None expected; tests stub the live send seam and assert no foreign request reaches it.
