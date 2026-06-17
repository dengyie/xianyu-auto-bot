# Phase 30 Design - Xianyu runtime direct status handler seams

## Goal

Add smoke coverage for the higher-level `XianyuAutoAsync` runtime seams that call the direct order-status handler entrypoints after chat parsing.

## Scope

- Cover the live `XianyuLive.handle_message(...)` path that calls:
  - `order_status_handler.handle_system_message(...)`
  - `order_status_handler.handle_red_reminder_message(...)` when the system-message path returns `False`
- Assert the runtime seam forwards the parsed `sid`, `buyer_id`, and `item_id` match context correctly.

## Why this phase

Phase 29 proved the runtime seam for `on_order_id_extracted(...)`, but the same live method still contains separate handoffs for direct system-message and red-reminder status handling.

Those handoffs are operationally important because a regression there would bypass the handler-level queueing and cancellation protections already covered in earlier phases.

## Planned test shape

- Extend `tests/smoke/test_xianyu_order_status_runtime_seam.py`.
- Reuse the lightweight `XianyuLive.__new__(XianyuLive)` setup and base64 sync payload pattern from phase 29.
- Keep the runtime path real through `handle_message(...)`, but patch unrelated seams to stay deterministic:
  - `is_sync_package(...)`
  - `is_chat_message(...)`
  - `_classify_message_route(...)`
  - `_extract_order_id(...)`
  - `_extract_order_message_context(...)`
  - `_preload_basic_order_info(...)`
  - `fetch_order_detail_info(...)`
  - `_maybe_force_refresh_order_detail_for_signal(...)`
- Use a self-sent system message (`send_user_id == myid`) so the test bypasses chat-message persistence noise and stays focused on the order-status runtime handoff.

## Assertions

- Direct system-message seam:
  - `handle_system_message(...)` is called once with the decoded message, runtime `cookie_id`, parsed `msg_time`, and `match_context` containing the expected `sid`, `buyer_id`, and `item_id`
- Red-reminder fallback seam:
  - when `handle_system_message(...)` returns `False` and the message has a non-terminal `redReminder` that does not trigger an earlier dedicated shortcut branch, `handle_red_reminder_message(...)` is called with the same runtime `match_context`

## Acceptance

- New targeted smoke tests pass.
- Full smoke suite passes.
- `compileall` passes for the existing validation scope.
- Production review reports no new blocking findings for the phase-30 diff.
