# Phase 94 Design - Admin Security Management Boundary Coverage

## Scope
- Add focused smoke coverage for:
  - `GET /admin/security/login-stats`
  - `POST /admin/security/unblock-ip/{ip}`
  - `POST /admin/security/unlock-user/{username}`
  - `POST /admin/security/blacklist-ip/{ip}`
  - `POST /admin/security/update-config`

## Verifiable Result
- Regular authenticated users are rejected from login security stats and mutation endpoints.
- Admin login security stats report blocked IPs, locked users, blacklisted IPs, and brute-force config.
- Admin unblock removes temporary blocked IP and blacklist state.
- Admin unlock clears locked-user state.
- Admin blacklist adds an IP to the blacklist.
- Admin config update accepts valid numeric keys and ignores invalid keys.

## Implementation Plan
- Keep production code unchanged unless tests expose a defect.
- Seed `login_ip_tracker`, `login_user_tracker`, and `ip_blacklist` directly for deterministic route-level assertions.
- Restore global brute-force config and trackers after the test to avoid cross-test leakage.
- Use existing admin and regular-user auth fixtures.

## Acceptance
- Targeted security hardening test passes.
- Full smoke suite passes.
- `compileall` passes.
- `git diff --check` passes.
- Checkpoint production review passes.
