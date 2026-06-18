# Phase 85 Design - Keywords Debug Metadata Admin Boundary

## Objective

Restrict the keywords table debug metadata endpoint to administrators and keep it reading from the active database connection.

## Scope

- `GET /debug/keywords-table-info`

## Acceptance Criteria

- Regular authenticated users receive `403`.
- Admin users can still read the keywords table metadata.
- The endpoint reads from the active `db_manager` connection instead of opening a separate SQLite connection by path.
- Targeted smoke tests pass, full smoke suite passes, compileall passes, `git diff --check` passes, and checkpoint production review passes.

## Design Notes

- This route exposes schema and database version metadata, so it belongs with admin/debug operations rather than regular user APIs.
- Reading through the active connection avoids incorrect behavior for in-memory DBs and keeps the route aligned with the running application state.

## Decision Record

- Decision: Change `/debug/keywords-table-info` from authenticated-user access to `require_admin` and use `db_manager.conn` for metadata queries.
- Rationale: Debug metadata is operational information, and a separate SQLite connection can inspect the wrong database when the app uses `:memory:`.
- Risk: Low; no frontend references were found, and admin behavior is preserved.
