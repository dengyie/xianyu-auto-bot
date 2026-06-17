# Phase 31 Design - Dedicated terminal red-reminder runtime shortcut seam

## Goal

Add smoke coverage for the early terminal red-reminder runtime shortcut inside `XianyuLive.handle_message(...)`.

## Scope

- Cover the live path where `red_reminder == "交易关闭"` triggers the dedicated early branch
- Assert that the runtime shortcut calls `order_status_handler.handle_red_reminder_order_status(...)`
- Assert that the runtime shortcut forwards the expected `buyer_id`, `item_id`, `cookie_id`, and `msg_time`

## Why this phase

Phase 30 intentionally avoided the `交易关闭` fixture because that value is consumed by an earlier dedicated branch and never reaches the later `handle_red_reminder_message(...)` fallback seam.

That means the dedicated terminal shortcut is now the remaining high-value runtime seam in this area that still lacks explicit smoke coverage.

## Planned test shape

- Extend `tests/smoke/test_xianyu_order_status_runtime_seam.py`
- Reuse the lightweight `XianyuLive.__new__(XianyuLive)` fixture and base64 sync payload helper
- Keep the runtime path real through `handle_message(...)`
- Use a self-sent system-like message with:
  - `message["3"]["redReminder"] == "交易关闭"`
  - a parsed `itemId` in `reminderUrl`
  - `senderUserId == myid`
- Assert the dedicated shortcut invokes `handle_red_reminder_order_status(...)`
- Assert the later `handle_system_message(...)` and `handle_red_reminder_message(...)` seams are not called in this branch

## Acceptance

- New targeted smoke tests pass
- Full smoke suite passes
- `compileall` passes for the existing validation scope
- Production review reports no new blocking findings for the phase-31 diff
