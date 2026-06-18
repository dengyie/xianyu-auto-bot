# Phase 87 Design - User Settings Scope Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /user-settings`
  - `PUT /user-settings/{key}`
  - `GET /user-settings/{key}`

## Verifiable Result
- Anonymous callers cannot access user settings.
- Two authenticated users can use the same setting key without overwriting each other.
- List and read endpoints return only the current user's value.
- Missing settings remain `404` for the current user.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Use existing auth fixtures for admin and regular user.
- Write the same key for both users with different values.
- Assert per-user list/read isolation and missing-key behavior.

## Acceptance
- Targeted authz matrix test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
