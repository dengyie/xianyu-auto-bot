# Phase 75 Design - User Backup and Settings Scope Coverage

## Objective

Lock down the remaining user-scoped backup/settings surface with focused smoke coverage and preserve explicit route status contracts.

## Scope

- `GET /user-settings`
- `PUT /user-settings/{key}`
- `GET /user-settings/{key}`
- `GET /backup/export`
- `POST /backup/import`
- User-level `DBManager.import_backup(...)`

## Acceptance Criteria

- User settings list/read/update only expose the authenticated user's keys.
- Missing `GET /user-settings/{key}` returns `404`, not a wrapped `500`.
- User backup export only includes the authenticated user's cookie-scoped data.
- User backup import rewrites imported `cookies.user_id` to the authenticated user.
- User backup import rewrites imported `cards`, `delivery_rules`, and `notification_channels` `user_id` values to the authenticated user.
- User backup import skips global `system_settings` in user-level imports.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Keep this phase bounded to backup/settings because the routes are adjacent user-scoped surfaces and currently lack direct smoke coverage.
- Do not alter system-level backup behavior (`user_id is None`).
- Preserve current file format compatibility by rewriting `user_id` columns only when those columns exist in the incoming table data.

## Decision Record

- Decision: Treat user-level backup imports as user-owned restores, not raw table imports.
- Rationale: A user import endpoint should not be able to write another user's `user_id` into restored resources or mutate global settings.
- Risk: Older backup files without `user_id` columns still import as before; they simply cannot set ownership on tables that lack ownership columns in the payload.
