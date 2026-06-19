# Phase 101 Slidex Integration Design

## Milestone

Make token-refresh slider verification use the external `dengyie/slidex` package by default and make fallback diagnostics explicit.

## Current Root Cause

Runtime logs show token refresh uses the `legacy` slider runtime because `import slidex` fails in the active Python environment. The account then repeatedly receives `FAIL_SYS_USER_VALIDATE`, the legacy solver reports `slider not found`, fallback sessions complete, and token refresh still reaches `captcha_max_retries_exceeded`.

## Goal

- Add `slidex` to project dependencies.
- Keep the existing runtime loader's `slidex -> legacy` compatibility pattern.
- Make the loader visible/testable so missing `slidex` is easy to diagnose.
- Preserve existing caller contract: `_handle_captcha_verification(...)` still receives a solver whose `solve(url)` returns `(success, cookies)`.

## P0/P1 Scope

- Dependency declaration for `slidex`.
- Regression tests proving `_load_token_refresh_slider_runtime()` selects `slidex` when available and falls back only when the package itself is missing.
- Minimal compatibility handling for `SlidexConfig` and `SliderSolver` constructor differences.

## Out of Scope

- Rewriting the token refresh state machine.
- Changing Xianyu retry/backoff policy.
- Claiming real-world Xianyu risk-control success without live validation.
- Deleting legacy solver files.

## Acceptance

- Unit/smoke tests cover runtime selection.
- Relevant smoke tests pass.
- `python -m compileall` passes for changed Python files.
- `git diff --check` passes.
