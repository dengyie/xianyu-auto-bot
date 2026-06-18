# Phase 100 Background CookieManager Handoff Design

## Milestone

Close the remaining background account-runtime handoff consistency gap and run production review.

## Data Flow Under Review

### Password login

`_execute_password_login(...)` starts a background thread. On successful browser login:

1. Slider returns cookie dict.
2. Cookies are merged with existing protected fields.
3. `db_manager.update_cookie_account_info(...)` persists account credentials and cookie value.
4. `cookie_manager.manager.add_cookie(...)` or `update_cookie(...)` should switch runtime state.
5. Session is marked `success`.

Current risk:

- The CookieManager handoff is wrapped in `try/except`, but the exception is only logged as a warning.
- The session is still marked `success`, so the UI can report login complete while the account runtime did not switch.
- If CookieManager returns a failed `Future`, the existing code also does not consume it.

Expected behavior:

- Runtime handoff failure after DB persistence must mark the password-login session `failed`, not `success`.

### Manual cookie import

`_execute_manual_cookie_import(...)` starts a background thread. On valid precheck or slider success:

1. Cookies are merged with protected fields.
2. DB is saved or updated.
3. CookieManager add/update starts or switches runtime state.
4. Session is marked `success`.

Current risk:

- Direct exceptions from add/update already bubble to the outer import handler and mark the session failed.
- Failed `Future` return values are not consumed, so delayed handoff failure can be missed.

Expected behavior:

- Manual import must consume CookieManager handoff results before marking session success.

## Test Plan

- Add a manual-import smoke test where CookieManager returns a failed `concurrent.futures.Future`; session should become `failed`.
- Add a password-login smoke test where CookieManager returns a failed `concurrent.futures.Future`; session should become `failed`.

## Implementation Plan

- Reuse `_consume_cookie_manager_handoff(...)` from Phase 99.
- In password login, consume the returned handoff result and treat failures as fatal for the session.
- In manual import, consume the returned handoff result before setting success.

## Out of Scope

- Rewriting the thread model.
- Browser/slider end-to-end automation with real Xianyu accounts.
- Changing the intentional post-success background cookie refresh best-effort behavior.
