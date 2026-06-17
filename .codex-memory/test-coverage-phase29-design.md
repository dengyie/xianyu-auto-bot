# Phase 29 Design - Xianyu runtime order-status seam coverage

## Goal

Add smoke coverage for the runtime seam in `XianyuAutoAsync` that bridges parsed live-message context into `OrderStatusHandler`.

## Scope

- Cover the live message entrypoint that:
  - decodes a sync package
  - extracts an `order_id`
  - extracts `sid`, `buyer_id`, and `item_id`
  - calls `order_status_handler.on_order_id_extracted(...)` with that `match_context`
- Keep the test focused on wiring behavior instead of downstream handler internals that already have dedicated coverage.

## Why this phase

Current coverage proves `OrderStatusHandler` can consume delayed binding context correctly, but it does not yet prove the real `XianyuAutoAsync` runtime path passes the parsed context through unchanged.

This seam is high value because a regression here would silently defeat many of the handler-level delayed-binding guarantees without failing their existing tests.

## Planned test shape

- Add a new smoke test file for the runtime seam.
- Instantiate `XianyuLive` via `__new__` to avoid heavy runtime bootstrapping.
- Use a minimal fake websocket and a base64-encoded sync payload.
- Stub only the unrelated runtime dependencies needed to reach the seam deterministically:
  - `is_sync_package(...)`
  - `_extract_order_id(...)`
  - `_extract_order_message_context(...)`
  - `_preload_basic_order_info(...)`
  - `fetch_order_detail_info(...)`
  - `is_chat_message(...)`
- Assert that `order_status_handler.on_order_id_extracted(...)` receives:
  - the extracted `order_id`
  - the instance `cookie_id`
  - the decoded message object
  - `match_context` containing the exact `sid`, `buyer_id`, and `item_id`

## Acceptance

- New targeted smoke test passes.
- Full smoke suite passes.
- `compileall` passes for the existing validation scope.
- Production review reports no new blocking findings for the phase-29 diff.
