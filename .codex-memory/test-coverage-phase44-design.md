# Phase 44 Design - history-sync job ownership boundaries

## Goal

Add focused route-level smoke coverage for the history-order sync job status and cancel endpoints when another user tries to access the job.

## Scope

- Extend `tests/smoke/test_order_history_sync.py`.
- Reuse the existing history-sync fake fetcher harness and multi-user auth fixtures.
- Target:
  - `GET /api/orders/history-sync/{job_id}`
  - `POST /api/orders/history-sync/{job_id}/cancel`

## Why This Phase

The history-sync route cluster already has smoke coverage for lifecycle, success, detail-refresh fallback, and live-runtime refresh.
The adjacent production contract is the ownership boundary on the created background job itself.

That boundary is valuable because the job snapshots include account scope, progress, and error information. A regression here would let one user inspect or cancel another user's sync task.

## Test Shape

- Create a history-sync job as one user.
- Attempt to query the job from a different user and assert a 403 response.
- Attempt to cancel the same job from a different user and assert a 403 response.
- Confirm the original job remains available to its owner after the foreign access attempts.

## Acceptance

- Targeted history-sync smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-44 diff.
