# Phase 102 Account Runtime Monitor Design

## Milestone

Account runtime monitoring that is near-real-time for operators while remaining read-only against Xianyu.

## Goal

Operators should see account connection, token refresh, keepalive, message-flow, and risk-control status from the admin UI without opening logs. Automatic monitoring must only read local runtime state from this service and must not trigger token refresh, session keepalive, history fetch, QR login, browser automation, or any request to official Xianyu endpoints.

## P0/P1 Scope

- Add explicit read-only monitoring metadata to runtime status responses.
- Add normalized Chinese risk/status summaries for states such as `FAIL_SYS_USER_VALIDATE`, `captcha_max_retries_exceeded`, and `password_login_backoff_wait`.
- Add focused smoke tests proving the runtime status endpoint returns monitoring-safe metadata and does not invoke active keepalive/refresh paths.
- Update the account UI diagnostics panel to auto-refresh only while the account section is visible.
- Add concise runtime badges to account list rows from already fetched local status data.
- Use conservative polling: active account diagnostics every 5 seconds, no auto polling outside the account section, and no automatic external recovery action.

## Out of Scope

- SSE/WebSocket push stream for status.
- Any automatic Xianyu probing or recovery.
- Changes to token refresh, keepalive, or captcha retry strategy.
- Broad frontend modularization or unrelated UI cleanup.
- Deleting unrelated untracked workspace files.

## Design

The backend keeps `/cookies/{cid}/runtime-status` as the source of truth and enriches `_build_live_runtime_status(...)` with read-only monitoring metadata. The endpoint continues to inspect `cookie_manager.manager.tasks`, `XianyuLive` fields, and database-owned cookie access only. It does not call any method that touches the Xianyu network.

The frontend reuses the existing account diagnostics panel. `loadAboutRuntimeStatus(...)` remains the only per-account refresh path. A timer runs only when `accounts-section` is active and a diagnostics account is selected. The timer refreshes every 5 seconds while the document is visible, pauses while hidden or outside the account section, and never calls `/session-keepalive` unless the user clicks the manual button.

Account rows show compact runtime badges derived from the `runtime_status` returned by `/cookies/details`. These badges are local snapshots; the diagnostics panel provides the actively refreshed detail view.

## Acceptance

- Smoke tests cover read-only monitoring metadata and risk-control summary output.
- Frontend code contains conservative polling controls and no automatic calls to keepalive/history/token endpoints.
- `venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/smoke/test_accounts.py -q -k runtime` passes.
- Compile and diff hygiene checks pass.