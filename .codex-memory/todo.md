# TODO

## In Progress
- [ ] Stage, commit, and push phase 40 finalize-after-send failure coverage to the draft GitHub PR

## Next
- [ ] Evaluate the pending-finalize replay path (`_get_pending_delivery_finalization_meta(...)` plus manual delivery retry) for direct route coverage
- [ ] Evaluate whether any broader route or service entrypoint still needs coverage beyond the now-covered runtime detail-refresh and message-handoff seams

## Done
- [x] Stage, commit, and push phase 39 manual-delivery reservation closure coverage to the draft GitHub PR
- [x] Stage, commit, and push phase 38 data-card reservation seam coverage to the draft GitHub PR
- [x] Stage, commit, and push phase 37 existing-order basic-info bypass seam coverage to the draft GitHub PR
- [x] Add authz/cookie isolation/file token/system settings smoke coverage
- [x] Add notification channel/template API regression tests
- [x] Add keyword/default reply matching regression tests
- [x] Add scheduled task ownership and validation tests
- [x] Add order delivery state transition integration tests
- [x] Add order history sync lifecycle smoke tests
- [x] Add refund/detail-failure order lifecycle smoke tests
- [x] Add pending-queue/live-instance order lifecycle smoke tests
- [x] Add system-message binding and unmatched cancellation resolution smoke tests
- [x] Add ambiguity rejection and stale pending cleanup smoke tests
- [x] Add terminal recent-fallback binding and ambiguity smoke tests
- [x] Add red-reminder terminal recent-fallback binding and ambiguity smoke tests
- [x] Add duplicate-`message_hash` plus unique-`strong_key` queue binding smoke tests for system messages and red reminders
- [x] Add enqueue-entry cleanup coverage for stale system-message and red-reminder pending state
- [x] Add bind-gap rejection coverage so old terminal pending messages stay queued instead of binding across large time gaps
- [x] Add terminal discard coverage for refund-cancelled system messages and cancelled red reminders already consumed by a different recent order
- [x] Add multi-update pending-consumption coverage for `on_order_details_fetched(...)`
- [x] Add batch pending-drain coverage for `process_all_pending_updates()`
- [x] Add mixed-success batch pending-drain coverage so one requeued bucket does not block later queued orders
- [x] Add mixed-result detail-fetched pending-update coverage so one failed queued update does not block a later valid update
- [x] Add direct system-message rollback guard coverage so a lower-priority update cannot roll back a shipped order
- [x] Add completed-terminal delayed-binding discard coverage so already-consumed completion messages are cleaned up instead of rebound
- [x] Add shipped-terminal delayed-binding discard coverage so already-consumed shipment messages are cleaned up instead of rebound
- [x] Add failed direct-backfill fallback coverage so no-order-id red reminders still queue when direct old-order update fails
- [x] Add failed direct system-backfill fallback coverage so no-order-id system messages still queue when direct old-order update fails
- [x] Add direct cancelled system-message backfill success coverage so unique old orders can be updated without queueing
- [x] Add ambiguous direct system-backfill fallback coverage so no-order-id system messages queue instead of mutating one of multiple matching old orders
- [x] Add ambiguous direct red-reminder backfill fallback coverage so no-order-id red reminders queue instead of mutating one of multiple matching old orders
- [x] Add missing-strong-key system-message fallthrough coverage so incomplete match context queues instead of attempting direct backfill
- [x] Add missing-strong-key red-reminder fallthrough coverage so incomplete match context queues instead of attempting direct backfill
- [x] Add runtime seam coverage so `XianyuLive.handle_message(...)` forwards parsed order match context into `OrderStatusHandler`
- [x] Add direct runtime seam coverage so `XianyuLive.handle_message(...)` forwards parsed match context into the system-message and red-reminder status handler entrypoints
- [x] Add terminal red-reminder runtime shortcut coverage so `XianyuLive.handle_message(...)` exercises `handle_red_reminder_order_status(...)` with the expected live context
- [x] Add successful detail-refresh seam coverage so `XianyuLive.fetch_order_detail_info(...)` persists the order and triggers the handler follow-up hooks
- [x] Add detail-refresh failure-isolation seam coverage so `XianyuLive.fetch_order_detail_info(...)` still returns fetched detail when handler follow-up raises after persistence
- [x] Add detail-refresh write-failure seam coverage so `XianyuLive.fetch_order_detail_info(...)` returns fetched detail but skips handler follow-up when persistence returns `False`
- [x] Add basic-order-info handler-failure seam coverage so `XianyuLive._auto_delivery(...)` still returns prepared delivery content when the post-write status helper raises
- [x] Add basic-order-info write-failure seam coverage so `XianyuLive._auto_delivery(...)` still returns prepared delivery content when the initial write returns `False`
- [x] Add existing-order basic-info bypass seam coverage so `XianyuLive._auto_delivery(...)` skips duplicate prewrite and handler side effects for persisted orders
- [x] Add data-card reservation success/failure seam coverage so `XianyuLive._auto_delivery(...)` returns reserved content and metadata only when a reservation is available
- [x] Add manual-delivery reservation closure coverage so data-card sends mark reservations sent on success and release them when post-send marking fails
- [x] Add finalize-after-send failure coverage so reservation-backed manual delivery units stay in `partial_pending_finalize` after send-side finalization fails
