# Phase 97 Design - Review Risk Closure

## Goal

Close the production review findings from Phase 96:

- forged `X-Forwarded-For` headers can weaken login brute-force protection
- audit-log query failures are reported as successful empty results
- request-level audit logging can grow the SQLite database without retention

## Scope

### In Scope

- Add a central trusted client IP helper.
- Use it in login/captcha security paths and audit logging.
- Default to `request.client.host` unless a trusted proxy is configured.
- Add a clear audit-log query failure path for `/admin/audit-logs`.
- Add audit-log retention cleanup with a configurable retention-days setting.
- Add focused smoke tests for the fixed risks.

### Out Of Scope

- Reverse proxy deployment changes.
- External log shipping.
- Audit log UI.
- Full route-by-route audit expansion.

## Trusted IP Contract

Environment variables:

- `TRUST_PROXY_HEADERS`: when truthy, forwarded headers may be used only if the direct peer is trusted.
- `TRUSTED_PROXY_IPS`: comma-separated exact IPs/CIDR ranges. Loopback is included by default for local reverse-proxy development.

If proxy trust is not enabled, `X-Forwarded-For` and `X-Real-IP` are ignored.

## Audit Query Contract

- DB query helper raises on query failure.
- Admin route returns `500` and `success=false` when query fails.
- Write-path audit persistence remains failure-isolated.

## Retention Contract

- System setting `audit_log_retention_days` defaults to `90`.
- `0` or a negative value disables deletion.
- Cleanup runs opportunistically after audit writes and can be called from tests.

## Acceptance

- Forged forwarded headers do not change login-failure tracking when proxy trust is disabled.
- Trusted proxy configuration accepts forwarded client IP.
- `/admin/audit-logs` fails loudly when DB query fails.
- Old audit logs can be pruned while recent logs stay.
- Existing smoke suite remains green.
