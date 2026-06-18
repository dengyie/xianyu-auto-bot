# Phase 80 Design - AI Config Preset Ownership Coverage

## Objective

Lock down user-scoped AI configuration presets, including sensitive API key and base URL fields.

## Scope

- `GET /ai-config-presets`
- `POST /ai-config-presets`
- `DELETE /ai-config-presets/{preset_id}`

## Acceptance Criteria

- Users only list their own AI config presets.
- Different users can use the same `preset_name` without overwriting or seeing each other's preset.
- A user cannot delete another user's preset by id.
- Owner delete still works.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- Keep production behavior unchanged because route and DB helpers already pass `user_id` through list/save/delete operations.
- Add focused smoke coverage around list/save/delete so API key carrying presets stay user-scoped.

## Decision Record

- Decision: Add route-level smoke coverage for AI config preset ownership without changing implementation.
- Rationale: Presets can store API keys and provider URLs, so user-level isolation needs an explicit regression even though the current DB helper filters by `user_id`.
- Risk: None expected; the test pins the existing route contract.
