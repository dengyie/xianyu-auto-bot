# Phase 96 Design - Unified Audit And Operation Logging

## Goal

Add a structured logging layer that records high-value user actions, request outcomes, data changes, and persistence results in one queryable place.

## Source Context

- Existing Loguru/file logs are useful for operators but hard to query by user/action/resource/result.
- Existing `delivery_logs` and `risk_control_logs` cover specific domains only.
- The `superpowers` package-design document favors normalized records, resolver-style central contracts, fallback visibility, and structured reporting. This phase applies the same idea to operational logging.

## Scope

### In Scope

- Add an `audit_logs` database table with indexes for time, actor, action, category, status, and resource.
- Add DB helpers to create and query audit logs.
- Add a central audit logger utility that:
  - normalizes event shape
  - redacts sensitive keys
  - stores structured JSON details
  - never breaks the primary request if audit persistence fails
- Add request middleware logging for API request outcomes.
- Add explicit audit events for high-value operations:
  - login success/failure
  - admin user management
  - admin log access/export
  - admin system/data/backup/security endpoints through request outcome logging
- Add admin-only query API for audit logs.
- Add smoke coverage proving audit logs are written, queryable, and redacted.

### Out Of Scope

- Full front-end analytics UI.
- External log shipping.
- Long-term retention and archival automation.
- Exhaustive instrumentation of every business route in one phase.

## Data Model

`audit_logs`:

- `id`
- `created_at`
- `category`
- `action`
- `status`
- `actor_user_id`
- `actor_username`
- `actor_is_admin`
- `client_ip`
- `request_method`
- `request_path`
- `resource_type`
- `resource_id`
- `duration_ms`
- `message`
- `details_json`

## Event Contract

Categories:

- `request`
- `auth`
- `admin`
- `data`
- `system`

Status values:

- `success`
- `failed`
- `denied`
- `error`

Details are structured JSON and must be redacted before persistence.

Sensitive keys include password, token, cookie, secret, key, authorization, proxy password, and captcha answers.

## Implementation Plan

1. Add schema and helpers in `db_manager/base.py`.
2. Add `utils/audit_logger.py` with `record_audit_event(...)`, redaction, request actor extraction, and status normalization.
3. Extend `reply_server` request middleware to persist request outcomes for non-static API/admin routes.
4. Add explicit login and selected admin-operation audit calls where the action/result is more meaningful than generic request logging.
5. Add `GET /admin/audit-logs` for admin querying.
6. Add focused smoke tests.

## Acceptance

- `audit_logs` table exists on init.
- Login success/failure creates `auth` events.
- Admin user management and request middleware create queryable events.
- Sensitive values do not appear in persisted details.
- Admin can query audit logs; regular users are denied.
- Existing smoke suite remains green.
